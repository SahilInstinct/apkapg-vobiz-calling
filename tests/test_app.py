"""Comprehensive tests for minimal Vobiz calling, storage, batches, exports, and UI."""

import csv
import io
import json
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.requests import Request

from app.api.auth import get_calling_service
from app.api.ui import require_local_dashboard_origin
from app.config import Settings, get_settings
from app.main import create_app
from app.models.batch import BatchItemStatus, BatchStatus
from app.services.batch_service import get_batch_manager
from app.services.calling_service import CallingService
from app.services.vobiz_service import VobizService
from app.storage.csv_storage import LEAD_CSV_COLUMNS, LeadCsvStorage
from app.storage.json_storage import CallJsonStorage, LeadJsonStorage
from app.utils.validators import normalize_phone_number


def test_phone_normalization():
    assert normalize_phone_number("9876543210") == "+919876543210"
    assert normalize_phone_number("09876543210") == "+919876543210"
    assert normalize_phone_number("+91 98765-43210") == "+919876543210"
    with pytest.raises(ValueError):
        normalize_phone_number("12345")


def test_health_and_ui_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    monkeypatch.setenv("API_AUTH_KEY", "secret-key")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/system/status").status_code == 200

        home = client.get("/")
        assert home.status_code == 200
        assert "ApkaPG Vobiz Calling Console" in home.text
        assert "secret-key" not in home.text

        assert client.post("/api/calls/initiate", json={"phone_number": "9876543210"}).status_code == 401
        ui_resp = client.post("/ui/calls/initiate", json={"phone_number": "9876543210"})
        assert ui_resp.status_code == 201

    remote_req = Request({"type": "http", "method": "POST", "path": "/ui/calls/initiate", "headers": [], "client": ("192.168.1.50", 1234)})
    with pytest.raises(HTTPException) as exc:
        require_local_dashboard_origin(remote_req, settings=Settings(app_env="development", ui_allow_docker_bridge=False))
    assert exc.value.status_code == 403
    get_settings.cache_clear()


def test_direct_vobiz_gather_silent_and_unanswered_leads(tmp_path, monkeypatch):
    audio_dir = tmp_path / "data" / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "intro.mp3").write_bytes(b"fake-intro")
    (audio_dir / "thank_you.mp3").write_bytes(b"fake-ty")

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "false")
    monkeypatch.setenv("VOBIZ_AUTH_ID", "MA_TEST")
    monkeypatch.setenv("VOBIZ_AUTH_TOKEN", "TOK_TEST")
    monkeypatch.setenv("VOBIZ_CALLER_ID", "+918071234567")
    monkeypatch.setenv("VOBIZ_WEBHOOK_SECRET", "vbz-sec")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://calling.example.com")
    monkeypatch.setenv("API_AUTH_KEY", "api-key")
    get_settings.cache_clear()

    def vobiz_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_uuid": "vbz-1", "message": "Call fired"})

    app = create_app()
    settings = get_settings()
    app.dependency_overrides[get_calling_service] = lambda: CallingService(
        settings=settings,
        call_storage=CallJsonStorage(settings.calls_history_file),
        lead_storage=LeadJsonStorage(settings.leads_json_file),
        lead_csv_storage=LeadCsvStorage(settings.leads_csv_file),
        vobiz_service=VobizService(settings=settings, transport=httpx.MockTransport(vobiz_handler)),
    )

    try:
        with TestClient(app) as client:
            # 1. GET/POST /answer + spoken reply + hangup
            c1 = client.post("/api/calls/initiate", headers={"X-API-Key": "api-key"}, json={"phone_number": "9876543210"}).json()["call"]["call_id"]
            ans = client.get(f"/api/vobiz/answer?call_id={c1}&token=vbz-sec")
            assert ans.status_code == 200
            assert "<Gather" in ans.text
            g1 = client.post(f"/api/vobiz/gather?call_id={c1}&token=vbz-sec", data={"Speech": "Yes I own Sunrise PG"})
            assert "<Play>https://calling.example.com/api/vobiz/audio/thank_you.mp3</Play>" in g1.text
            client.post(f"/api/vobiz/hangup?call_id={c1}&token=vbz-sec", data={"CallStatus": "completed", "Duration": "20"})

            # 2. Picked up silently (no speech)
            c2 = client.post("/api/calls/initiate", headers={"X-API-Key": "api-key"}, json={"phone_number": "9876543211"}).json()["call"]["call_id"]
            client.post(f"/api/vobiz/answer?call_id={c2}&token=vbz-sec")
            client.post(f"/api/vobiz/hangup?call_id={c2}&token=vbz-sec", data={"CallStatus": "completed", "Duration": "5"})

            # 3. Not picked up (unanswered)
            c3 = client.post("/api/calls/initiate", headers={"X-API-Key": "api-key"}, json={"phone_number": "9876543212"}).json()["call"]["call_id"]
            client.post(f"/api/vobiz/hangup?call_id={c3}&token=vbz-sec", data={"CallStatus": "no-answer", "Duration": "0"})

            # 4. Hangup completed with duration > 0 marks picked_up=True even if /answer was skipped
            c4 = client.post("/api/calls/initiate", headers={"X-API-Key": "api-key"}, json={"phone_number": "9876543213"}).json()["call"]["call_id"]
            client.post(f"/api/vobiz/hangup?call_id={c4}&token=vbz-sec", data={"CallStatus": "completed", "Duration": "21"})

            l1 = client.get(f"/api/leads/{c1}").json()
            assert l1["picked_up"] is True
            assert l1["user_input"] == "Yes I own Sunrise PG"
            assert l1["call_duration_seconds"] == 20

            l2 = client.get(f"/api/leads/{c2}").json()
            assert l2["picked_up"] is True
            assert l2["user_input"] is None

            l3 = client.get(f"/api/leads/{c3}").json()
            assert l3["picked_up"] is False
            assert l3["user_input"] is None
            assert l3["call_status"] == "unanswered"

            l4 = client.get(f"/api/leads/{c4}").json()
            assert l4["picked_up"] is True
            assert l4["call_duration_seconds"] == 21

            picked_only = client.get("/api/leads?picked_up=true").json()
            assert picked_only["count"] == 3

            csv_text = client.get("/api/leads/export/csv").text
            reader = csv.DictReader(io.StringIO(csv_text))
            assert tuple(reader.fieldnames or []) == LEAD_CSV_COLUMNS
            assert len(list(reader)) == 4
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_direct_record_audio_mode(tmp_path, monkeypatch):
    audio_dir = tmp_path / "data" / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "intro.mp3").write_bytes(b"fake-intro")

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "false")
    monkeypatch.setenv("VOBIZ_CAPTURE_MODE", "record_audio")
    monkeypatch.setenv("VOBIZ_AUTH_ID", "MA_TEST")
    monkeypatch.setenv("VOBIZ_AUTH_TOKEN", "TOK_TEST")
    monkeypatch.setenv("VOBIZ_CALLER_ID", "+918071234567")
    monkeypatch.setenv("VOBIZ_WEBHOOK_SECRET", "vbz-sec")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://calling.example.com")
    monkeypatch.setenv("API_AUTH_KEY", "api-key")
    get_settings.cache_clear()

    def vobiz_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"request_uuid": "vbz-rec-1"})
        return httpx.Response(200, content=b"ID3-caller-mp3")

    app = create_app()
    settings = get_settings()
    app.dependency_overrides[get_calling_service] = lambda: CallingService(
        settings=settings,
        call_storage=CallJsonStorage(settings.calls_history_file),
        lead_storage=LeadJsonStorage(settings.leads_json_file),
        lead_csv_storage=LeadCsvStorage(settings.leads_csv_file),
        vobiz_service=VobizService(settings=settings, transport=httpx.MockTransport(vobiz_handler)),
    )
    try:
        with TestClient(app) as client:
            c1 = client.post("/api/calls/initiate", headers={"X-API-Key": "api-key"}, json={"phone_number": "9876543210"}).json()["call"]["call_id"]
            assert "<Record " in client.post(f"/api/vobiz/answer?call_id={c1}&token=vbz-sec").text
            rec = client.post(
                f"/api/vobiz/recording-ready?call_id={c1}&token=vbz-sec",
                data={"RecordUrl": "https://api.vobiz.ai/rec/1.mp3", "RecordingID": "r1"},
            )
            assert rec.status_code == 200
            assert (tmp_path / "data" / "recordings" / f"{c1}.mp3").read_bytes() == b"ID3-caller-mp3"
            lead = client.get(f"/api/leads/{c1}").json()
            assert lead["picked_up"] is True
            assert lead["recording_file"] is not None
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_sequential_mock_batch_saves_picked_up_and_input(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    get_settings.cache_clear()

    manager = get_batch_manager()
    old_dur = manager.mock_call_duration_seconds
    manager.mock_call_duration_seconds = 0.05
    try:
        app = create_app()
        with TestClient(app) as client:
            resp = client.post(
                "/ui/batches",
                json={
                    "delay_seconds": 0,
                    "csv_content": "9876543210,Ramesh,Sunrise PG\n9876543210\nbad\n9876543211\n",
                },
            )
            assert resp.status_code == 201
            batch_id = resp.json()["batch"]["batch_id"]
            final = None
            for _ in range(40):
                final = client.get(f"/api/batches/{batch_id}").json()
                if final["batch"]["status"] == BatchStatus.COMPLETED.value:
                    break
                time.sleep(0.05)

            assert final["batch"]["status"] == BatchStatus.COMPLETED.value
            assert final["batch"]["items"][1]["status"] == BatchItemStatus.SKIPPED_DUPLICATE.value
            assert final["batch"]["items"][2]["status"] == BatchItemStatus.INVALID.value

            leads = json.loads((tmp_path / "data" / "leads" / "leads.json").read_text(encoding="utf-8"))
            assert len(leads) == 2
            assert all(l["picked_up"] is True and l["user_input"] == "Yes, I am the PG owner" for l in leads)
    finally:
        manager.mock_call_duration_seconds = old_dur
        get_settings.cache_clear()