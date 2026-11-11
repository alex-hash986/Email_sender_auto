"""Настройки рассылки из переменных окружения и локального файла .env."""

from pathlib import Path
from typing import Annotated

from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().with_name(".env"),
        env_file_encoding="utf-8-sig",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )

    openai_api_key: SecretStr
    openai_base_url: HttpUrl | None = None
    openai_model: NonEmptyText = "gpt-4o-mini"

    smtp_server: NonEmptyText
    smtp_port: int = Field(ge=1, le=65535)
    smtp_username: NonEmptyText
    smtp_password: SecretStr
    sender_email: NonEmptyText
    sender_name: NonEmptyText

    delay_min_seconds: float = Field(default=3, ge=0, allow_inf_nan=False)
    delay_max_seconds: float = Field(default=7, ge=0, allow_inf_nan=False)

    @field_validator("openai_api_key", "smtp_password")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Укажите непустое значение")
        return value

    @model_validator(mode="after")
    def validate_delay_range(self) -> "Settings":
        if self.delay_max_seconds < self.delay_min_seconds:
            raise ValueError("DELAY_MAX_SECONDS должен быть не меньше DELAY_MIN_SECONDS")
        return self
