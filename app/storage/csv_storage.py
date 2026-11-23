"""Safe CSV persistence for minimal lead records (data/leads/leads.csv)."""

import csv
import io
from pathlib import Path
from typing import Any

from app.models.lead import LeadRecord
from app.storage.file_lock import atomic_write_text, file_lock

LEAD_CSV_COLUMNS: tuple[str, ...] = (
    "call_id",
    "campaign_id",
    "phone_number",
    "owner_name",
    "pg_name",
    "picked_up",
    "user_input",
    "recording_file",
    "recording_url",
    "call_status",
    "call_duration_seconds",
    "created_at",
    "updated_at",
)


def lead_to_csv_row(lead: LeadRecord) -> dict[str, Any]:
    data = lead.model_dump(mode="json")
    return {
        col: ("" if data.get(col) is None else data.get(col))
        for col in LEAD_CSV_COLUMNS
    }


class LeadCsvStorage:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def upsert_lead(self, lead: LeadRecord) -> None:
        with file_lock(self.file_path):
            existing_rows: list[dict[str, str]] = []
            if self.file_path.exists():
                content = self.file_path.read_text(encoding="utf-8")
                if content.strip():
                    existing_rows = list(csv.DictReader(io.StringIO(content)))

            new_row = {col: str(lead_to_csv_row(lead)[col]) for col in LEAD_CSV_COLUMNS}
            replaced = False
            for idx, row in enumerate(existing_rows):
                if row.get("call_id") == lead.call_id:
                    existing_rows[idx] = new_row
                    replaced = True
                    break

            if not replaced:
                existing_rows.append(new_row)

            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer,
                fieldnames=list(LEAD_CSV_COLUMNS),
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for row in existing_rows:
                writer.writerow({col: row.get(col, "") for col in LEAD_CSV_COLUMNS})

            atomic_write_text(self.file_path, buffer.getvalue(), encoding="utf-8")