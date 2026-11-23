"""Sequential calling batch API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import require_api_key
from app.config import Settings, get_settings
from app.models.batch import BatchCreateRequest, BatchRecord
from app.services.batch_service import (
    BatchNotFoundError,
    BatchRunnerManager,
    BatchValidationError,
    get_batch_manager,
)
from app.storage.json_storage import BatchJsonStorage

router = APIRouter(prefix="/api/batches", tags=["Batches"])


class BatchResponse(BaseModel):
    batch: BatchRecord
    summary: dict[str, int]


class BatchListResponse(BaseModel):
    count: int
    batches: list[BatchResponse]


def to_batch_response(batch: BatchRecord) -> BatchResponse:
    return BatchResponse(batch=batch, summary=batch.summary)


@router.post(
    "",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
async def create_batch(
    payload: BatchCreateRequest,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.create_batch(settings=settings, request=payload))
    except BatchValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("", response_model=BatchListResponse)
async def list_batches(settings: Settings = Depends(get_settings)) -> BatchListResponse:
    batches = BatchJsonStorage(settings.batches_json_file).list_batches()
    return BatchListResponse(count=len(batches), batches=[to_batch_response(b) for b in batches])


@router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch(batch_id: str, settings: Settings = Depends(get_settings)) -> BatchResponse:
    batch = BatchJsonStorage(settings.batches_json_file).get_by_batch_id(batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Batch '{batch_id}' not found.")
    return to_batch_response(batch)


@router.post("/{batch_id}/pause", response_model=BatchResponse, dependencies=[Depends(require_api_key)])
async def pause_batch(
    batch_id: str,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.pause_batch(settings=settings, batch_id=batch_id))
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{batch_id}/resume", response_model=BatchResponse, dependencies=[Depends(require_api_key)])
async def resume_batch(
    batch_id: str,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.resume_batch(settings=settings, batch_id=batch_id))
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{batch_id}/stop", response_model=BatchResponse, dependencies=[Depends(require_api_key)])
async def stop_batch(
    batch_id: str,
    settings: Settings = Depends(get_settings),
    manager: BatchRunnerManager = Depends(get_batch_manager),
) -> BatchResponse:
    try:
        return to_batch_response(manager.stop_batch(settings=settings, batch_id=batch_id))
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc