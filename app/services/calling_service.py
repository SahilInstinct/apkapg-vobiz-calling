"""Outbound calling orchestration and minimal lead persistence (picked_up + user_input)."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.call import CallEventEntry, CallInitiateRequest, CallRecord, CallStatus
from app.models.lead import LeadRecord
from app.services.vobiz_service import VobizError, VobizService
from app.storage.csv_storage import LeadCsvStorage
from app.storage.json_storage import CallJsonStorage, LeadJsonStorage
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TERMINAL_CALL_STATUSES = {
    CallStatus.COMPLETED,
    CallStatus.FAILED,
    CallStatus.BUSY,
    CallStatus.UNANSWERED,
    CallStatus.CANCELED,
}


class CallNotFoundError(Exception):
    """Raised when a requested call record cannot be found."""


class CallingService:
    def __init__(
        self,
        settings: Settings,
        call_storage: CallJsonStorage,
        lead_storage: LeadJsonStorage,
        lead_csv_storage: LeadCsvStorage,
        vobiz_service: VobizService,
    ) -> None:
        self.settings = settings
        self.call_storage = call_storage
        self.lead_storage = lead_storage
        self.lead_csv_storage = lead_csv_storage
        self.vobiz_service = vobiz_service

    def _upsert_minimal_lead(
        self,
        call_record: CallRecord,
        picked_up: bool | None = None,
        user_input: str | None = None,
        recording_file: str | None = None,
        recording_url: str | None = None,
    ) -> LeadRecord:
        existing = self.lead_storage.get_by_call_id(call_record.call_id)
        was_picked_up = (
            picked_up
            if picked_up is not None
            else (
                existing.picked_up
                if existing
                else call_record.status in {CallStatus.ANSWERED, CallStatus.IN_PROGRESS}
            )
        )

        lead = LeadRecord(
            call_id=call_record.call_id,
            campaign_id=call_record.campaign_id,
            phone_number=call_record.phone_number,
            owner_name=call_record.owner_name,
            pg_name=call_record.pg_name,
            picked_up=was_picked_up,
            user_input=user_input if user_input is not None else (existing.user_input if existing else None),
            recording_file=recording_file if recording_file is not None else (existing.recording_file if existing else None),
            recording_url=recording_url if recording_url is not None else (existing.recording_url if existing else None),
            call_status=call_record.status.value,
            call_duration_seconds=call_record.duration_seconds,
        )
        saved = self.lead_storage.upsert_lead(lead)
        self.lead_csv_storage.upsert_lead(saved)
        return saved

    async def initiate_call(self, request: CallInitiateRequest) -> CallRecord:
        call_record = self.call_storage.create_call(request)

        if self.settings.mock_providers:
            event = CallEventEntry(
                event_key=f"{call_record.call_id}:mock:ringing",
                source="mock_vobiz",
                status=CallStatus.RINGING,
                raw_status="ringing",
            )
            updated, _ = self.call_storage.record_event(
                call_id=call_record.call_id,
                event=event,
                provider_call_id=f"mock-vobiz-{call_record.call_id}",
            )
            logger.info("Mock call initiated: call_id=%s", updated.call_id)
            return updated

        try:
            vobiz_result = await self.vobiz_service.trigger_outbound_call(
                call_id=call_record.call_id,
                phone_number=request.phone_number,
            )
        except VobizError as exc:
            failure_event = CallEventEntry(
                event_key=f"{call_record.call_id}:vobiz:initiate_failed",
                source="vobiz",
                status=CallStatus.FAILED,
                raw_status="failed",
                details={"error": str(exc)},
            )
            failed_record, _ = self.call_storage.record_event(
                call_id=call_record.call_id,
                event=failure_event,
                failure_reason=str(exc),
            )
            self._upsert_minimal_lead(call_record=failed_record, picked_up=False)
            logger.error("Call initiation failed for %s: %s", call_record.call_id, exc)
            raise

        accepted_event = CallEventEntry(
            event_key=f"{call_record.call_id}:vobiz:triggered:{vobiz_result.request_uuid}",
            source="vobiz",
            status=CallStatus.INITIATED,
            raw_status="initiated",
            details={"request_uuid": vobiz_result.request_uuid},
        )
        updated, _ = self.call_storage.record_event(
            call_id=call_record.call_id,
            event=accepted_event,
            provider_call_id=vobiz_result.request_uuid,
        )
        logger.info("Vobiz call triggered: call_id=%s request_uuid=%s", updated.call_id, vobiz_result.request_uuid)
        return updated

    def _find_call(self, call_id: str | None, provider_call_id: str | None) -> CallRecord | None:
        if call_id:
            found = self.call_storage.get_by_call_id(call_id)
            if found:
                return found
        if provider_call_id:
            return self.call_storage.get_by_provider_call_id(provider_call_id)
        return None

    def process_status_event(
        self,
        call_id: str | None,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> tuple[CallRecord, bool]:
        provider_call_id = (
            payload.get("CallUUID")
            or payload.get("RequestUUID")
            or payload.get("call_uuid")
            or payload.get("provider_call_id")
        )
        record = self._find_call(call_id=call_id, provider_call_id=provider_call_id)
        if record is None:
            raise CallNotFoundError(f"No matching call found for call_id={call_id}.")

        raw_status = payload.get("CallStatus") or payload.get("status") or "completed"
        mapped_status = self.vobiz_service.map_status(str(raw_status))

        raw_duration = (
            payload.get("Duration")
            or payload.get("BillDuration")
            or payload.get("duration_seconds")
        )
        duration_seconds: int | None = None
        if raw_duration is not None and str(raw_duration).strip() != "":
            try:
                duration_seconds = max(0, int(float(raw_duration)))
            except (ValueError, TypeError):
                duration_seconds = None

        if event_id:
            event_key = f"event:{event_id}"
        else:
            canonical = json.dumps(
                {
                    "call_id": record.call_id,
                    "status": mapped_status.value,
                    "raw_status": str(raw_status),
                    "duration": duration_seconds,
                    "provider_call_id": provider_call_id,
                },
                sort_keys=True,
            )
            event_key = f"hash:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"

        event = CallEventEntry(
            event_key=event_key,
            source=str(payload.get("source", "vobiz")),
            status=mapped_status,
            raw_status=str(raw_status),
            timestamp=datetime.now(timezone.utc),
            details=dict(payload),
        )
        updated, applied = self.call_storage.record_event(
            call_id=record.call_id,
            event=event,
            duration_seconds=duration_seconds,
            provider_call_id=str(provider_call_id) if provider_call_id else None,
        )

        if applied:
            if mapped_status == CallStatus.ANSWERED:
                self._upsert_minimal_lead(call_record=updated, picked_up=True)
            elif mapped_status in _TERMINAL_CALL_STATUSES:
                if mapped_status in {
                    CallStatus.FAILED,
                    CallStatus.BUSY,
                    CallStatus.UNANSWERED,
                    CallStatus.CANCELED,
                }:
                    explicit_picked: bool | None = False
                elif mapped_status == CallStatus.COMPLETED and (duration_seconds or 0) > 0:
                    explicit_picked = True
                else:
                    explicit_picked = None

                self._upsert_minimal_lead(
                    call_record=updated,
                    picked_up=explicit_picked,
                    user_input=payload.get("user_input"),
                )
        return updated, applied

    def save_speech_input(self, call_id: str | None, form_data: dict[str, Any]) -> CallRecord:
        provider_call_id = form_data.get("CallUUID") or form_data.get("RequestUUID")
        record = self._find_call(call_id=call_id, provider_call_id=provider_call_id)
        if record is None:
            raise CallNotFoundError(f"No matching call found for call_id={call_id}.")

        speech_text = str(form_data.get("Speech") or form_data.get("Transcript") or "").strip()
        digits = str(form_data.get("Digits") or "").strip()
        user_input = speech_text or (f"Digits: {digits}" if digits else None)

        digest = hashlib.sha256(
            f"{record.call_id}:gather:{user_input or ''}".encode("utf-8")
        ).hexdigest()[:12]
        event = CallEventEntry(
            event_key=f"vobiz:gather:{digest}",
            source="vobiz",
            status=CallStatus.IN_PROGRESS,
            raw_status="speech_captured",
            details={"user_input": user_input, "confidence": form_data.get("SpeechConfidenceScore")},
        )
        updated, _ = self.call_storage.record_event(
            call_id=record.call_id,
            event=event,
            provider_call_id=str(provider_call_id) if provider_call_id else None,
        )
        self._upsert_minimal_lead(
            call_record=updated,
            picked_up=True,
            user_input=user_input,
        )
        return updated

    async def save_recording_input(
        self, call_id: str | None, form_data: dict[str, Any]
    ) -> tuple[CallRecord, Path | None]:
        provider_call_id = form_data.get("CallUUID") or form_data.get("RequestUUID")
        record = self._find_call(call_id=call_id, provider_call_id=provider_call_id)
        if record is None:
            raise CallNotFoundError(f"No matching call found for call_id={call_id}.")

        record_url = str(form_data.get("RecordUrl") or "").strip() or None
        recording_id = str(form_data.get("RecordingID") or "").strip()

        saved_path: Path | None = None
        if record_url:
            ext = ".wav" if ".wav" in record_url.lower() else ".mp3"
            dest = self.settings.recordings_dir / f"{record.call_id}{ext}"
            saved_path = await self.vobiz_service.download_recording(record_url, dest)

        event = CallEventEntry(
            event_key=f"vobiz:recording:{recording_id or record.call_id}",
            source="vobiz",
            status=CallStatus.IN_PROGRESS,
            raw_status="recording_ready",
            details={"recording_url": record_url, "local_file": str(saved_path) if saved_path else None},
        )
        updated, _ = self.call_storage.record_event(
            call_id=record.call_id,
            event=event,
            provider_call_id=str(provider_call_id) if provider_call_id else None,
        )
        self._upsert_minimal_lead(
            call_record=updated,
            picked_up=True,
            recording_file=str(saved_path) if saved_path else None,
            recording_url=record_url,
        )
        return updated, saved_path

    def get_call(self, call_id: str) -> CallRecord:
        record = self.call_storage.get_by_call_id(call_id)
        if record is None:
            raise CallNotFoundError(f"Call '{call_id}' not found.")
        return record

    def list_calls(self) -> list[CallRecord]:
        return self.call_storage.list_calls()