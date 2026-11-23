"""Querying, filtering, and exporting minimal leads as JSON and CSV."""

import csv
import io
import json
from typing import Any

from app.models.lead import LeadRecord
from app.storage.csv_storage import LEAD_CSV_COLUMNS, lead_to_csv_row
from app.storage.json_storage import LeadJsonStorage


class LeadNotFoundError(Exception):
    """Raised when a requested lead record cannot be found."""


class ExportService:
    def __init__(self, lead_storage: LeadJsonStorage) -> None:
        self.lead_storage = lead_storage

    def list_leads(
        self,
        picked_up: bool | None = None,
        campaign_id: str | None = None,
    ) -> list[LeadRecord]:
        leads = self.lead_storage.list_leads()
        if picked_up is not None:
            leads = [lead for lead in leads if lead.picked_up is picked_up]
        if campaign_id is not None:
            leads = [lead for lead in leads if lead.campaign_id == campaign_id]
        return leads

    def get_lead(self, call_id: str) -> LeadRecord:
        lead = self.lead_storage.get_by_call_id(call_id)
        if lead is None:
            raise LeadNotFoundError(f"Lead for call '{call_id}' not found.")
        return lead

    def export_json_string(
        self,
        picked_up: bool | None = None,
        campaign_id: str | None = None,
    ) -> str:
        leads = self.list_leads(picked_up=picked_up, campaign_id=campaign_id)
        payload: list[dict[str, Any]] = [lead.model_dump(mode="json") for lead in leads]
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    def export_csv_string(
        self,
        picked_up: bool | None = None,
        campaign_id: str | None = None,
    ) -> str:
        leads = self.list_leads(picked_up=picked_up, campaign_id=campaign_id)
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(LEAD_CSV_COLUMNS),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for lead in leads:
            writer.writerow(lead_to_csv_row(lead))
        return buffer.getvalue()