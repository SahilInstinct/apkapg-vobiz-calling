"""Vobiz REST API client, VobizXML generator, recording downloader, and status mapper."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import Settings
from app.models.call import CallStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)

_STATUS_MAP: dict[str, CallStatus] = {
    "initiated": CallStatus.INITIATED,
    "queued": CallStatus.INITIATED,
    "ringing": CallStatus.RINGING,
    "answered": CallStatus.ANSWERED,
    "in-progress": CallStatus.IN_PROGRESS,
    "in_progress": CallStatus.IN_PROGRESS,
    "completed": CallStatus.COMPLETED,
    "ended": CallStatus.COMPLETED,
    "hangup": CallStatus.COMPLETED,
    "busy": CallStatus.BUSY,
    "no-answer": CallStatus.UNANSWERED,
    "no_answer": CallStatus.UNANSWERED,
    "unanswered": CallStatus.UNANSWERED,
    "timeout": CallStatus.UNANSWERED,
    "failed": CallStatus.FAILED,
    "error": CallStatus.FAILED,
    "rejected": CallStatus.FAILED,
    "canceled": CallStatus.CANCELED,
    "cancelled": CallStatus.CANCELED,
}


class VobizError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class VobizCallTriggerResult:
    request_uuid: str
    message: str
    raw_response: dict[str, Any]


class VobizService:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.vobiz_api_base_url.strip().strip('"').strip("'").rstrip("/")
        self._transport = transport

    def map_status(self, raw_status: str | None) -> CallStatus:
        if not raw_status:
            return CallStatus.FAILED
        return _STATUS_MAP.get(raw_status.strip().lower(), CallStatus.FAILED)

    def _clean_public_base_url(self) -> str:
        base = self.settings.public_base_url.strip().strip('"').strip("'").strip().rstrip("/")
        if not base:
            raise VobizError(
                "PUBLIC_BASE_URL must be set to your public HTTPS URL.",
                status_code=400,
            )
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise VobizError(
                f"PUBLIC_BASE_URL '{base}' is not a valid URL. It must start with https://",
                status_code=400,
            )
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
            raise VobizError(
                "PUBLIC_BASE_URL cannot be localhost/127.0.0.1. Use your Cloudflare/Azure HTTPS URL.",
                status_code=400,
            )
        return base

    def _public_url(self, path: str, params: dict[str, str] | None = None) -> str:
        base = self._clean_public_base_url()
        query = dict(params or {})
        secret = self.settings.vobiz_webhook_secret.strip().strip('"').strip("'")
        if secret:
            query["token"] = secret
        qs = f"?{urlencode(query)}" if query else ""
        return f"{base}{path}{qs}"

    def resolve_intro_audio_url(self) -> str:
        explicit = self.settings.vobiz_intro_audio_url.strip().strip('"').strip("'")
        if explicit:
            return explicit
        local_intro = self.settings.audio_dir / "intro.mp3"
        if local_intro.exists():
            base = self._clean_public_base_url()
            return f"{base}/api/vobiz/audio/intro.mp3"
        raise VobizError(
            "No intro recording found. Set VOBIZ_INTRO_AUDIO_URL or place intro.mp3 in data/audio/.",
            status_code=400,
        )

    def resolve_thankyou_audio_url(self) -> str | None:
        explicit = self.settings.vobiz_thankyou_audio_url.strip().strip('"').strip("'")
        if explicit:
            return explicit
        local_ty = self.settings.audio_dir / "thank_you.mp3"
        if local_ty.exists():
            try:
                base = self._clean_public_base_url()
                return f"{base}/api/vobiz/audio/thank_you.mp3"
            except VobizError:
                return None
        return None

    def get_status_summary(self) -> dict[str, Any]:
        intro_exists = bool(self.settings.vobiz_intro_audio_url.strip()) or (
            self.settings.audio_dir / "intro.mp3"
        ).exists()
        ready = all(
            [
                bool(self.settings.vobiz_auth_id.strip()),
                bool(self.settings.vobiz_auth_token.strip()),
                bool(self.settings.vobiz_caller_id.strip()),
                bool(self.settings.public_base_url.strip()),
                intro_exists,
            ]
        )
        return {
            "auth_id_configured": bool(self.settings.vobiz_auth_id.strip()),
            "auth_token_configured": bool(self.settings.vobiz_auth_token.strip()),
            "caller_id_configured": bool(self.settings.vobiz_caller_id.strip()),
            "public_base_url_configured": bool(self.settings.public_base_url.strip()),
            "intro_audio_configured": intro_exists,
            "capture_mode": self.settings.vobiz_capture_mode,
            "ready": ready,
        }

    def _auth_headers(self) -> dict[str, str]:
        auth_id = self.settings.vobiz_auth_id.strip().strip('"').strip("'")
        auth_token = self.settings.vobiz_auth_token.strip().strip('"').strip("'")
        if not auth_id or not auth_token:
            raise VobizError(
                "VOBIZ_AUTH_ID and VOBIZ_AUTH_TOKEN must be set in .env.",
                status_code=400,
            )
        return {
            "X-Auth-ID": auth_id,
            "X-Auth-Token": auth_token,
            "Content-Type": "application/json",
        }

    async def trigger_outbound_call(
        self,
        call_id: str,
        phone_number: str,
    ) -> VobizCallTriggerResult:
        headers = self._auth_headers()
        caller_id = self.settings.vobiz_caller_id.strip().strip('"').strip("'")
        if not caller_id:
            raise VobizError("VOBIZ_CALLER_ID must be set in .env.", status_code=400)

        self.resolve_intro_audio_url()
        base = self._clean_public_base_url()
        logger.info("Triggering Vobiz call %s using public base %s", call_id, base)

        payload = {
            "from": caller_id,
            "to": phone_number,
            "answer_url": self._public_url("/api/vobiz/answer", {"call_id": call_id}),
            "answer_method": "POST",
            "hangup_url": self._public_url("/api/vobiz/hangup", {"call_id": call_id}),
            "hangup_method": "POST",
            "ring_url": self._public_url("/api/vobiz/ring", {"call_id": call_id}),
            "ring_method": "POST",
        }

        auth_id = headers["X-Auth-ID"]
        path = f"/api/v1/Account/{auth_id}/Call/"
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=20.0, transport=self._transport
            ) as client:
                response = await client.post(path, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise VobizError(f"Failed to reach Vobiz API: {exc}") from exc

        if response.status_code >= 400:
            raise VobizError(
                f"Vobiz rejected outbound call ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        request_uuid = str(data.get("request_uuid") or data.get("call_uuid") or "")
        if not request_uuid:
            raise VobizError(f"Vobiz response missing request_uuid: {data}", status_code=502)

        return VobizCallTriggerResult(
            request_uuid=request_uuid,
            message=str(data.get("message", "Call fired")),
            raw_response=data,
        )

    def build_answer_xml(self, call_id: str) -> str:
        intro_url = escape(self.resolve_intro_audio_url())
        if self.settings.vobiz_capture_mode == "record_audio":
            action_url = quoteattr(
                self._public_url("/api/vobiz/recording-action", {"call_id": call_id})
            )
            callback_url = quoteattr(
                self._public_url("/api/vobiz/recording-ready", {"call_id": call_id})
            )
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"  <Play>{intro_url}</Play>\n"
                f'  <Record action={action_url} method="POST" callbackUrl={callback_url} callbackMethod="POST" '
                'fileFormat="mp3" maxLength="30" timeout="5" playBeep="false" finishOnKey="#"/>\n'
                "  <Hangup/>\n"
                "</Response>\n"
            )

        action_url = quoteattr(
            self._public_url("/api/vobiz/gather", {"call_id": call_id})
        )
        interim_url = quoteattr(
            self._public_url("/api/vobiz/gather-interim", {"call_id": call_id})
        )
        language = quoteattr(self.settings.vobiz_speech_language)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Gather action={action_url} method="POST" inputType="speech" '
            f'speechModel="default" language={language} executionTimeout="30" '
            f'speechEndTimeout="auto" interimSpeechResultsCallback={interim_url}>\n'
            f"    <Play>{intro_url}</Play>\n"
            "  </Gather>\n"
            "  <Hangup/>\n"
            "</Response>\n"
        )

    def build_after_capture_xml(self) -> str:
        thankyou_url = self.resolve_thankyou_audio_url()
        if thankyou_url:
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"  <Play>{escape(thankyou_url)}</Play>\n"
                "  <Hangup/>\n"
                "</Response>\n"
            )
        return '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Hangup/>\n</Response>\n'

    async def download_recording(self, record_url: str, dest_path: Path) -> Path:
        headers = {
            "X-Auth-ID": self.settings.vobiz_auth_id.strip().strip('"').strip("'"),
            "X-Auth-Token": self.settings.vobiz_auth_token.strip().strip('"').strip("'"),
        }
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with httpx.AsyncClient(
                timeout=30.0, transport=self._transport, follow_redirects=True
            ) as client:
                response = await client.get(record_url, headers=headers)
        except httpx.HTTPError as exc:
            raise VobizError(f"Failed to download recording: {exc}") from exc

        if response.status_code >= 400:
            raise VobizError(
                f"Vobiz recording download failed ({response.status_code})",
                status_code=response.status_code,
            )

        dest_path.write_bytes(response.content)
        logger.info("Saved recording to %s", dest_path)
        return dest_path