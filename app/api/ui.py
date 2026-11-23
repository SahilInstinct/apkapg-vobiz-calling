"""Localhost-only dashboard UI routes."""

import ipaddress
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.api.auth import get_calling_service
from app.api.batches import BatchResponse, to_batch_response
from app.api.calls import CallInitiateResponse
from app.config import Settings, get_settings
from app.models.batch import BatchCreateRequest
from app.models.call import CallInitiateRequest
from app.services.batch_service import (
    BatchNotFoundError,
    BatchRunnerManager,
    BatchValidationError,
    get_batch_manager,
)
from app.services.calling_service import CallingService
from app.services.vobiz_service import VobizError

router = APIRouter(tags=["UI"], include_in_schema=False)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_local_dashboard_origin(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    client_host = request.client.host if request.client else ""
    if client_host in _LOCAL_HOSTS:
        return
    if settings.ui_allow_docker_bridge and settings.app_env != "production":
        try:
            ip = ipaddress.ip_address(client_host)
            if ip.is_loopback or ip.is_private:
                return
        except ValueError:
            pass
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Dashboard actions are restricted to localhost. Use /api/* with X-API-Key.",
    )


@router.get("/")
async def operator_dashboard() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


@router.post(
    "/ui/calls/initiate",
    response_model=CallInitiateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_local_dashboard_origin)],
)
async def ui_initiate_call(
    payload: CallInitiateRequest,
    service: CallingService = Depends(get_calling_service),
) -> CallInitiateResponse:
    try:
        return CallInitiateResponse(call=await service.initiate_call(payload))
    except VobizError as exc:
        upstream_status = exc.status_code if exc.status_code in (400, 401, 403, 404, 422) else 502
        raise HTTPException(status_code=upstream_status, detail=str(exc)) from exc


@router.post(
    "/ui/batches",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_local_dashboard_origin)],
)
async def ui_create_batch(
    payload: BatchCreateRequest,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.create_batch(settings=settings, request=payload))
    except BatchValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/ui/batches/{batch_id}/pause", response_model=BatchResponse, dependencies=[Depends(require_local_dashboard_origin)])
async def ui_pause_batch(
    batch_id: str,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.pause_batch(settings=settings, batch_id=batch_id))
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/ui/batches/{batch_id}/resume", response_model=BatchResponse, dependencies=[Depends(require_local_dashboard_origin)])
async def ui_resume_batch(
    batch_id: str,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.resume_batch(settings=settings, batch_id=batch_id))
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/ui/batches/{batch_id}/stop", response_model=BatchResponse, dependencies=[Depends(require_local_dashboard_origin)])
async def ui_stop_batch(
    batch_id: str,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.stop_batch(settings=settings, batch_id=batch_id))
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc