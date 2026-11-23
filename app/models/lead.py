"""Minimal lead record storing whether the call was picked up and the caller's input."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import normalize_optional_text, normalize_phone_number


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LeadRecord(BaseModel):
    """Minimal lead record saved locally in JSON and CSV."""

    call_id: str
    campaign_id: str | None = Field(default=None)
    phone_number: str
    owner_name: str | None = Field(default=None)
    pg_name: str | None = Field(default=None)
    picked_up: bool = Field(default=False)
    user_input: str | None = Field(default=None)
    recording_file: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)
    call_status: str | None = Field(default=None)
    call_duration_seconds: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone_number(value)

    @field_validator(
        "campaign_id",
        "owner_name",
        "pg_name",
        "user_input",
        "recording_file",
        "recording_url",
        "call_status",
        mode="before",
    )
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)