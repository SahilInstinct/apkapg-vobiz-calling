"""Vobiz REST API client, dynamic <Speak>/<Play> VobizXML generator, and status mapper."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode, urlparse
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import Settings
from app.models.call import CallRecord, CallStatus
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

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


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

    @staticmethod
    def render_script_template(template: str, call_record: CallRecord | None = None) -> str:
        """Render a TTS script with {owner_name}, {pg_name}, {notes}, {phone_number}."""
        owner = (call_record.owner_name or "").strip() if call_record else ""
        pg = (call_record.pg_name or "").strip() if call_record else ""
        notes = (call_record.notes or "").strip() if call_record else ""
        phone = (call_record.phone_number or "").strip() if call_record else ""

        cleaned = template.strip()
        if cleaned.upper() == "AUTO":
            greeting = f"Hello {owner}" if owner else "Hello"
            pg_part = f" regarding {pg}" if pg else ""
            return (
                f"{greeting}, this is calling from ApkaPG{pg_part}. "
                "Am I speaking with the PG owner or manager?"
            )

        values = {
            "owner_name": owner,
            "pg_name": pg,
            "notes": notes,
            "phone_number": phone,
        }

        def _replace(match: re.Match) -> str:
            return values.get(match.group(1), "")

        rendered = _PLACEHOLDER_RE.sub(_replace, cleaned)
        rendered = re.sub(r"\s{2,}", " ", rendered)
        rendered = re.sub(r"\s+([,.\?!])", r"\1", rendered)
        return rendered.strip()

    def resolve_intro_speak_text(self, call_record: CallRecord | None = None) -> str | None:
        if call_record and call_record.script:
            return self.render_script_template(call_record.script, call_record)
        configured = self.settings.vobiz_intro_text.strip().strip('"').strip("'")
        if configured:
            return self.render_script_template(configured, call_record)
        return None

    def resolve_intro_audio_url(self) -> str:
        explicit = self.settings.vobiz_intro_audio_url.strip().strip('"').strip("'")
        if explicit:
            return explicit
        local_intro = self.settings.audio_dir / "intro.mp3"
        if local_intro.exists():
            base = self._clean_public_base_url()
            return f"{base}/api/vobiz/audio/intro.mp3"
        raise VobizError(
            "No intro script or audio found. Set VOBIZ_INTRO_TEXT in .env or place intro.mp3 in data/audio/.",
            status_code=400,
        )

    def resolve_thankyou_speak_text(self, call_record: CallRecord | None = None) -> str | None:
        configured = self.settings.vobiz_thankyou_text.strip().strip('"').strip("'")
        if configured:
            return self.render_script_template(configured, call_record)
        return None

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

    def _has_global_intro(self) -> bool:
        return bool(
            self.settings.vobiz_intro_text.strip()
            or self.settings.vobiz_intro_audio_url.strip()
            or (self.settings.audio_dir / "intro.mp3").exists()
        )

    def get_status_summary(self) -> dict[str, Any]:
        intro_ready = self._has_global_intro()
        prompt_type = "speak_tts" if self.settings.vobiz_intro_text.strip() else "audio_mp3"
        ready = all(
            [
                bool(self.settings.vobiz_auth_id.strip()),
                bool(self.settings.vobiz_auth_token.strip()),
                bool(self.settings.vobiz_caller_id.strip()),
                bool(self.settings.public_base_url.strip()),
                intro_ready,
            ]
        )
        return {
            "auth_id_configured": bool(self.settings.vobiz_auth_id.strip()),
            "auth_token_configured": bool(self.settings.vobiz_auth_token.strip()),
            "caller_id_configured": bool(self.settings.vobiz_caller_id.strip()),
            "public_base_url_configured": bool(self.settings.public_base_url.strip()),
            "intro_configured": intro_ready,
            "prompt_type": prompt_type,
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
        has_custom_script: bool = False,
    ) -> VobizCallTriggerResult:
        headers = self._auth_headers()
        caller_id = self.settings.vobiz_caller_id.strip().strip('"').strip("'")
        if not caller_id:
            raise VobizError("VOBIZ_CALLER_ID must be set in .env.", status_code=400)

        if not has_custom_script and not self.settings.vobiz_intro_text.strip():
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

    def _build_prompt_element(self, call_record: CallRecord | None = None) -> str:
        speak_text = self.resolve_intro_speak_text(call_record)
        if speak_text:
            voice = quoteattr(self.settings.vobiz_speak_voice)
            lang = quoteattr(self.settings.vobiz_speech_language)
            return f'<Speak voice={voice} language={lang} loop="1">{escape(speak_text)}</Speak>'
        intro_url = escape(self.resolve_intro_audio_url())
        return f"<Play>{intro_url}</Play>"

    def _build_closing_elements(self, call_record: CallRecord | None = None) -> list[str]:
        thankyou_text = self.resolve_thankyou_speak_text(call_record)
        if thankyou_text:
            voice = quoteattr(self.settings.vobiz_speak_voice)
            lang = quoteattr(self.settings.vobiz_speech_language)
            return [
                f'<Speak voice={voice} language={lang} loop="1">{escape(thankyou_text)}</Speak>',
                "<Hangup/>",
            ]
        thankyou_url = self.resolve_thankyou_audio_url()
        if thankyou_url:
            return [f"<Play>{escape(thankyou_url)}</Play>", "<Hangup/>"]
        return ["<Hangup/>"]

    def build_answer_xml(self, call_id: str, call_record: CallRecord | None = None) -> str:
        prompt_xml = self._build_prompt_element(call_record)

        if self.settings.vobiz_capture_mode == "record_audio":
            callback_url = quoteattr(
                self._public_url("/api/vobiz/recording-ready", {"call_id": call_id})
            )
            silence_timeout = max(2, min(60, self.settings.vobiz_no_input_timeout))
            max_len = self.settings.vobiz_max_record_seconds
            closing = "\n  ".join(self._build_closing_elements(call_record))
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"  {prompt_xml}\n"
                f'  <Record callbackUrl={callback_url} callbackMethod="POST" '
                f'fileFormat="mp3" maxLength="{max_len}" timeout="{silence_timeout}" '
                'playBeep="false" finishOnKey="#"/>\n'
                f"  {closing}\n"
                "</Response>\n"
            )

        action_url = quoteattr(
            self._public_url("/api/vobiz/gather", {"call_id": call_id})
        )
        language = quoteattr(self.settings.vobiz_speech_language)
        # Vobiz <Gather> executionTimeout allowed range is 5..60 seconds
        gather_timeout = max(5, min(60, self.settings.vobiz_no_input_timeout))
        speech_end = quoteattr(self.settings.vobiz_speech_end_timeout)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Gather action={action_url} method="POST" inputType="speech" '
            f'speechModel="default" language={language} executionTimeout="{gather_timeout}" '
            f"speechEndTimeout={speech_end}>\n"
            f"    {prompt_xml}\n"
            "  </Gather>\n"
            "  <Hangup/>\n"
            "</Response>\n"
        )

    def build_after_capture_xml(self, call_record: CallRecord | None = None) -> str:
        closing = "\n  ".join(self._build_closing_elements(call_record))
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  {closing}\n</Response>\n'

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