"""API key authentication and service dependency factories."""

import hmac

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.services.calling_service import CallingService
from app.services.export_service import ExportService
from app.services.vobiz_service import VobizService
from app.storage.csv_storage import LeadCsvStorage
from app.storage.json_storage import CallJsonStorage, LeadJsonStorage


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.api_auth_key.strip()
    if not expected:
        if settings.app_env == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server API_AUTH_KEY is not configured.",
            )
        return

    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key.",
        )


def get_calling_service(settings: Settings = Depends(get_settings)) -> CallingService:
    return CallingService(
        settings=settings,
        call_storage=CallJsonStorage(settings.calls_history_file),
        lead_storage=LeadJsonStorage(settings.leads_json_file),
        lead_csv_storage=LeadCsvStorage(settings.leads_csv_file),
        vobiz_service=VobizService(settings=settings),
    )


def get_export_service(settings: Settings = Depends(get_settings)) -> ExportService:
    return ExportService(lead_storage=LeadJsonStorage(settings.leads_json_file))