"""Pydantic models for sequential batch outbound calling."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import normalize_optional_text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class BatchItemStatus(str, Enum):
    PENDING = "pending"
    DIALING = "dialing"
    COMPLETED = "completed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    INVALID = "invalid"
    FAILED = "failed"
    CANCELED = "canceled"


class BatchCreateRequest(BaseModel):
    campaign_id: str | None = Field(default=None, max_length=80)
    csv_content: str = Field(..., min_length=1)
    delay_seconds: int = Field(default=3, ge=0, le=120)
    script: str | None = Field(default=None, max_length=1000)
    auto_start: bool = Field(default=True)

    @field_validator("campaign_id", "script", mode="before")
    @classmethod
    def clean_optional_fields(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class BatchItemRecord(BaseModel):
    row_number: int
    raw_phone: str
    phone_number: str | None = None
    owner_name: str | None = None
    pg_name: str | None = None
    notes: str | None = None
    script: str | None = None
    status: BatchItemStatus = Field(default=BatchItemStatus.PENDING)
    call_id: str | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=_utc_now)


class BatchRecord(BaseModel):
    batch_id: str
    campaign_id: str | None = None
    script: str | None = None
    status: BatchStatus = Field(default=BatchStatus.PENDING)
    delay_seconds: int = Field(default=3, ge=0, le=120)
    items: list[BatchItemRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            "total": len(self.items),
            "pending": 0,
            "dialing": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "canceled": 0,
        }
        for item in self.items:
            if item.status == BatchItemStatus.PENDING:
                counts["pending"] += 1
            elif item.status == BatchItemStatus.DIALING:
                counts["dialing"] += 1
            elif item.status == BatchItemStatus.COMPLETED:
                counts["completed"] += 1
            elif item.status == BatchItemStatus.SKIPPED_DUPLICATE:
                counts["skipped"] += 1
            elif item.status in {BatchItemStatus.FAILED, BatchItemStatus.INVALID}:
                counts["failed"] += 1
            elif item.status == BatchItemStatus.CANCELED:
                counts["canceled"] += 1
        return counts