"""Environment-driven application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="ApkaPG Vobiz Calling System")
    app_env: Literal["development", "staging", "production", "test"] = Field(
        default="development"
    )
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8001, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )

    api_auth_key: str = Field(default="")
    ui_allow_docker_bridge: bool = Field(default=False)
    data_dir: Path = Field(default=Path("data"))

    mock_providers: bool = Field(default=True)
    public_base_url: str = Field(default="")

    vobiz_api_base_url: str = Field(default="https://api.vobiz.ai")
    vobiz_auth_id: str = Field(default="")
    vobiz_auth_token: str = Field(default="")
    vobiz_caller_id: str = Field(default="")
    vobiz_webhook_secret: str = Field(default="")

    vobiz_capture_mode: Literal["gather_speech", "record_audio"] = Field(
        default="gather_speech"
    )
    vobiz_speech_language: str = Field(default="en-IN")
    vobiz_speak_voice: Literal["WOMAN", "MAN"] = Field(default="WOMAN")
    vobiz_no_input_timeout: int = Field(default=3, ge=2, le=180)
    vobiz_speech_end_timeout: str = Field(default="2")
    vobiz_max_record_seconds: int = Field(default=30, ge=5, le=300)
    vobiz_intro_text: str = Field(default="")
    vobiz_thankyou_text: str = Field(default="")
    vobiz_intro_audio_url: str = Field(default="")
    vobiz_thankyou_audio_url: str = Field(default="")

    @property
    def leads_dir(self) -> Path:
        return self.data_dir / "leads"

    @property
    def calls_dir(self) -> Path:
        return self.data_dir / "calls"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def calls_history_file(self) -> Path:
        return self.calls_dir / "call_history.json"

    @property
    def leads_json_file(self) -> Path:
        return self.leads_dir / "leads.json"

    @property
    def leads_csv_file(self) -> Path:
        return self.leads_dir / "leads.csv"

    @property
    def batches_json_file(self) -> Path:
        return self.batches_dir / "batches.json"

    def ensure_data_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.leads_dir,
            self.calls_dir,
            self.batches_dir,
            self.audio_dir,
            self.recordings_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def get_secret_values(self) -> list[str]:
        candidates = [
            self.api_auth_key,
            self.vobiz_auth_token,
            self.vobiz_webhook_secret,
        ]
        return [value for value in candidates if value and len(value) >= 4]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()