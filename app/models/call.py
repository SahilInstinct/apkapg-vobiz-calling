"""Call lifecycle Pydantic models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import normalize_optional_text, normalize_phone_number


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CallStatus(str, Enum):
    INITIATED = "initiated"
    RINGING = "ringing"
    ANSWERED = "answered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    UNANSWERED = "unanswered"
    CANCELED = "canceled"


class CallInitiateRequest(BaseModel):
    phone_number: str
    owner_name: str | None = Field(default=None, max_length=120)
    pg_name: str | None = Field(default=None, max_length=160)
    campaign_id: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=500)
    script: str | None = Field(default=None, max_length=1000)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone_number(value)

    @field_validator("owner_name", "pg_name", "campaign_id", "notes", "script", mode="before")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class CallEventEntry(BaseModel):
    event_key: str
    source: str
    status: CallStatus
    raw_status: str | None = None
    timestamp: datetime = Field(default_factory=_utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


class CallRecord(BaseModel):
    call_id: str
    provider_call_id: str | None = None
    campaign_id: str | None = None
    phone_number: str
    owner_name: str | None = None
    pg_name: str | None = None
    notes: str | None = None
    script: str | None = None
    status: CallStatus = Field(default=CallStatus.INITIATED)
    duration_seconds: int | None = Field(default=None, ge=0)
    failure_reason: str | None = None
    events: list[CallEventEntry] = Field(default_factory=list)
    processed_event_keys: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone_number(value)