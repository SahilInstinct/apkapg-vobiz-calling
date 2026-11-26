"""Vobiz Voice XML and webhook callback endpoints."""

import hmac
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.api.auth import get_calling_service
from app.config import Settings, get_settings
from app.services.calling_service import CallNotFoundError, CallingService
from app.services.vobiz_service import VobizError, VobizService
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/vobiz", tags=["Vobiz"])


def _verify_vobiz_token(
    token: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.vobiz_webhook_secret.strip()
    if not expected:
        return
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Vobiz token.")


async def _parse_vobiz_params(request: Request) -> dict[str, Any]:
    """Merge URL query parameters and form/JSON body (supports GET and POST)."""
    merged: dict[str, Any] = dict(request.query_params)
    raw_bytes = await request.body()
    if raw_bytes:
        if "application/json" in request.headers.get("content-type", "").lower():
            body_data = await request.json()
            if isinstance(body_data, dict):
                merged.update(body_data)
        else:
            parsed = parse_qs(raw_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
            for k, v in parsed.items():
                merged[k] = v[0] if len(v) == 1 else v
    return merged


@router.api_route("/audio/{filename}", methods=["GET", "HEAD"])
async def serve_audio_file(filename: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename.")
    audio_root = settings.audio_dir.resolve()
    target = (audio_root / filename).resolve()
    if target.parent != audio_root or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file not found.")
    media_type = "audio/wav" if Path(filename).suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(target, media_type=media_type)


@router.api_route("/ring", methods=["GET", "POST"], dependencies=[Depends(_verify_vobiz_token)])
async def vobiz_ring_callback(
    request: Request,
    call_id: str | None = Query(default=None),
    service: CallingService = Depends(get_calling_service),
) -> dict[str, Any]:
    params = await _parse_vobiz_params(request)
    logger.info("Vobiz /ring callback: call_id=%s status=%s", call_id or params.get("call_id"), params.get("CallStatus"))
    try:
        record, _ = service.process_status_event(
            call_id=call_id or params.get("call_id"),
            payload={**params, "status": params.get("CallStatus") or "ringing", "source": "vobiz"},
        )
        return {"received": True, "call_id": record.call_id, "status": record.status.value}
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.api_route("/answer", methods=["GET", "POST"], dependencies=[Depends(_verify_vobiz_token)])
async def vobiz_answer_webhook(
    request: Request,
    call_id: str = Query(...),
    service: CallingService = Depends(get_calling_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    params = await _parse_vobiz_params(request)
    logger.info("Vobiz /answer callback (%s): call_id=%s CallUUID=%s", request.method, call_id, params.get("CallUUID"))
    try:
        call_record, _ = service.process_status_event(
            call_id=call_id,
            payload={**params, "status": "answered", "source": "vobiz"},
        )
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        xml_body = VobizService(settings=settings).build_answer_xml(
            call_id=call_id,
            call_record=call_record,
        )
    except VobizError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(content=xml_body, media_type="application/xml")


@router.api_route("/gather", methods=["GET", "POST"], dependencies=[Depends(_verify_vobiz_token)])
async def vobiz_gather_webhook(
    request: Request,
    call_id: str | None = Query(default=None),
    service: CallingService = Depends(get_calling_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    params = await _parse_vobiz_params(request)
    logger.info(
        "Vobiz /gather callback (%s): call_id=%s params=%s",
        request.method,
        call_id or params.get("call_id"),
        {k: v for k, v in params.items() if k != "token"},
    )
    resolved_id = call_id or params.get("call_id")
    try:
        call_record = service.save_speech_input(call_id=resolved_id, form_data=params)
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(
        content=VobizService(settings=settings).build_after_capture_xml(call_record=call_record),
        media_type="application/xml",
    )


@router.api_route("/recording-ready", methods=["GET", "POST"], dependencies=[Depends(_verify_vobiz_token)])
async def vobiz_recording_ready_webhook(
    request: Request,
    call_id: str | None = Query(default=None),
    service: CallingService = Depends(get_calling_service),
) -> dict[str, Any]:
    params = await _parse_vobiz_params(request)
    logger.info(
        "Vobiz /recording-ready callback: call_id=%s params=%s",
        call_id or params.get("call_id"),
        {k: v for k, v in params.items() if k != "token"},
    )
    try:
        record, saved_path = await service.save_recording_input(
            call_id=call_id or params.get("call_id"),
            form_data=params,
        )
        return {
            "received": True,
            "call_id": record.call_id,
            "saved_recording": str(saved_path) if saved_path else None,
        }
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except VobizError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.api_route("/hangup", methods=["GET", "POST"], dependencies=[Depends(_verify_vobiz_token)])
async def vobiz_hangup_webhook(
    request: Request,
    call_id: str | None = Query(default=None),
    service: CallingService = Depends(get_calling_service),
) -> dict[str, Any]:
    params = await _parse_vobiz_params(request)
    logger.info(
        "Vobiz /hangup callback: call_id=%s CallStatus=%s Duration=%s HangupCause=%s",
        call_id or params.get("call_id"),
        params.get("CallStatus") or params.get("status"),
        params.get("Duration") or params.get("BillDuration"),
        params.get("HangupCause"),
    )
    try:
        record, applied = service.process_status_event(
            call_id=call_id or params.get("call_id"),
            payload={
                **params,
                "status": params.get("CallStatus") or params.get("status") or "completed",
                "source": "vobiz",
            },
        )
        return {"received": True, "call_id": record.call_id, "status": record.status.value, "duplicate": not applied}
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc