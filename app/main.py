"""FastAPI application entry point for ApkaPG Vobiz Calling System."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.batches import router as batches_router
from app.api.calls import router as calls_router
from app.api.leads import router as leads_router
from app.api.ui import router as ui_router
from app.api.vobiz import router as vobiz_router
from app.config import Settings, get_settings
from app.services.batch_service import get_batch_manager
from app.services.vobiz_service import VobizService
from app.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def _check_directory_writable(path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    setup_logging(level=settings.log_level, secrets=settings.get_secret_values())
    settings.ensure_data_dirs()
    logger.info("Starting %s (env=%s, mock=%s)", settings.app_name, settings.app_env, settings.mock_providers)
    yield
    await get_batch_manager().shutdown()
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        description="Outbound recording playback & caller response collection via Vobiz.",
        version="1.0.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info("%s %s -> %d (%.2f ms)", request.method, request.url.path, response.status_code, duration_ms)
        return response

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @application.get("/health", tags=["System"])
    async def health_check() -> JSONResponse:
        current = get_settings()
        storage = {
            "leads_dir": _check_directory_writable(current.leads_dir),
            "calls_dir": _check_directory_writable(current.calls_dir),
            "batches_dir": _check_directory_writable(current.batches_dir),
            "recordings_dir": _check_directory_writable(current.recordings_dir),
        }
        healthy = all(storage.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "healthy" if healthy else "degraded",
                "service": current.app_name,
                "mock_providers": current.mock_providers,
                "storage": storage,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @application.get("/api/system/status", tags=["System"])
    async def system_status() -> dict[str, Any]:
        current = get_settings()
        return {
            "mock_providers": current.mock_providers,
            "vobiz": VobizService(current).get_status_summary(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    application.include_router(ui_router)
    application.include_router(vobiz_router)
    application.include_router(calls_router)
    application.include_router(leads_router)
    application.include_router(batches_router)
    return application


app = create_app()