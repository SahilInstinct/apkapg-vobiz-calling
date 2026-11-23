"""Lead query and JSON/CSV export API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.api.auth import get_export_service
from app.models.lead import LeadRecord
from app.services.export_service import ExportService, LeadNotFoundError

router = APIRouter(prefix="/api/leads", tags=["Leads"])


class LeadListResponse(BaseModel):
    count: int
    leads: list[LeadRecord]


@router.get("", response_model=LeadListResponse)
async def list_leads(
    picked_up: bool | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    service: ExportService = Depends(get_export_service),
) -> LeadListResponse:
    leads = service.list_leads(picked_up=picked_up, campaign_id=campaign_id)
    return LeadListResponse(count=len(leads), leads=leads)


@router.get("/export/json")
async def export_leads_json(
    picked_up: bool | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    service: ExportService = Depends(get_export_service),
) -> Response:
    content = service.export_json_string(picked_up=picked_up, campaign_id=campaign_id)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="apkapg_leads.json"'},
    )


@router.get("/export/csv")
async def export_leads_csv(
    picked_up: bool | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    service: ExportService = Depends(get_export_service),
) -> Response:
    content = service.export_csv_string(picked_up=picked_up, campaign_id=campaign_id)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="apkapg_leads.csv"'},
    )


@router.get("/{call_id}", response_model=LeadRecord)
async def get_lead(
    call_id: str,
    service: ExportService = Depends(get_export_service),
) -> LeadRecord:
    try:
        return service.get_lead(call_id)
    except LeadNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc