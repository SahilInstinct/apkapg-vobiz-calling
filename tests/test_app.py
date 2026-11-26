"""Comprehensive tests for Vobiz <Speak>/<Play>, fast timeouts, RecordFile, and UI."""

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
        assert client.post("/api/calls/initiate", json={"phone_number": "9876543210"}).status_code == 401
        assert client.post("/ui/calls/initiate", json={"phone_number": "9876543210"}).status_code == 201

    remote_req = Request({"type": "http", "method": "POST", "path": "/ui/calls/initiate", "headers": [], "client": ("192.168.1.50", 1234)})
    with pytest.raises(HTTPException) as exc:
        require_local_dashboard_origin(remote_req, settings=Settings(app_env="development", ui_allow_docker_bridge=False))
    assert exc.value.status_code == 403
    get_settings.cache_clear()


def test_dynamic_speak_tts_and_fast_gather_timeouts(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "false")
    monkeypatch.setenv("VOBIZ_AUTH_ID", "MA_TEST")
    monkeypatch.setenv("VOBIZ_AUTH_TOKEN", "TOK_TEST")
    monkeypatch.setenv("VOBIZ_CALLER_ID", "+918071234567")
    monkeypatch.setenv("VOBIZ_WEBHOOK_SECRET", "vbz-sec")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://calling.example.com")
    monkeypatch.setenv("API_AUTH_KEY", "api-key")
    monkeypatch.setenv("VOBIZ_INTRO_TEXT", "AUTO")
    monkeypatch.setenv("VOBIZ_THANKYOU_TEXT", "Thank you {owner_name}!")
    monkeypatch.setenv("VOBIZ_SPEAK_VOICE", "MAN")
    monkeypatch.setenv("VOBIZ_NO_INPUT_TIMEOUT", "3")
    monkeypatch.setenv("VOBIZ_SPEECH_END_TIMEOUT", "2")
    get_settings.cache_clear()

    def vobiz_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_uuid": "vbz-tts-1"})

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
            c1 = client.post(
                "/api/calls/initiate",
                headers={"X-API-Key": "api-key"},
                json={"phone_number": "9876543210", "owner_name": "Ramesh", "pg_name": "Sai & Sons PG"},
            ).json()["call"]["call_id"]
            ans1 = client.post(f"/api/vobiz/answer?call_id={c1}&token=vbz-sec")
            assert ans1.status_code == 200
            assert 'executionTimeout="5"' in ans1.text  # clamped to Vobiz min 5s
            assert 'speechEndTimeout="2"' in ans1.text
            assert "Hello Ramesh, this is calling from ApkaPG regarding Sai &amp; Sons PG." in ans1.text

            g1 = client.post(f"/api/vobiz/gather?call_id={c1}&token=vbz-sec", data={"Speech": "Yes"})
            assert "Thank you Ramesh!</Speak>" in g1.text
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_record_audio_mode_supports_record_file_and_3s_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "false")
    monkeypatch.setenv("VOBIZ_CAPTURE_MODE", "record_audio")
    monkeypatch.setenv("VOBIZ_INTRO_TEXT", "AUTO")
    monkeypatch.setenv("VOBIZ_NO_INPUT_TIMEOUT", "3")
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
            ans = client.post(f"/api/vobiz/answer?call_id={c1}&token=vbz-sec")
            assert 'timeout="3"' in ans.text

            # Vobiz sends RecordFile in callbackUrl
            rec = client.post(
                f"/api/vobiz/recording-ready?call_id={c1}&token=vbz-sec",
                data={"RecordFile": "https://media.vobiz.ai/recordings/r1.mp3", "RecordingID": "r1"},
            )
            assert rec.status_code == 200
            assert (tmp_path / "data" / "recordings" / f"{c1}.mp3").read_bytes() == b"ID3-caller-mp3"
            lead = client.get(f"/api/leads/{c1}").json()
            assert lead["picked_up"] is True
            assert lead["recording_url"] == "https://media.vobiz.ai/recordings/r1.mp3"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()