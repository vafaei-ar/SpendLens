from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: SecretStr = Field(
        validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_allowed_user_ids_raw: str = Field(
        validation_alias="TELEGRAM_ALLOWED_USER_IDS"
    )
    data_dir: Path = Field(
        default=Path("data"),
        validation_alias="SPENDLENS_DATA_DIR",
    )
    max_source_bytes: int = Field(
        default=25 * 1024 * 1024,
        validation_alias="SPENDLENS_MAX_SOURCE_BYTES",
        ge=1,
    )

    extraction_enabled: bool = Field(
        default=True,
        validation_alias="SPENDLENS_EXTRACTION_ENABLED",
    )

    local_ocr_primary_model: str = Field(
        default="mlx-community/PaddleOCR-VL-1.6-4bit",
        validation_alias="LOCAL_OCR_PRIMARY_MODEL",
    )
    local_ocr_secondary_enabled: bool = Field(
        default=True,
        validation_alias="LOCAL_OCR_SECONDARY_ENABLED",
    )
    local_ocr_secondary_model: str = Field(
        default="mlx-community/dots.ocr-4bit",
        validation_alias="LOCAL_OCR_SECONDARY_MODEL",
    )
    local_ocr_max_tokens: int = Field(
        default=4096,
        validation_alias="LOCAL_OCR_MAX_TOKENS",
        ge=256,
    )

    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias="OLLAMA_BASE_URL",
    )
    ollama_structurer_model: str = Field(
        default="qwen3:4b-instruct",
        validation_alias=AliasChoices(
            "OLLAMA_STRUCTURER_MODEL",
            "OLLAMA_MODEL",
        ),
    )
    ollama_timeout_seconds: float = Field(
        default=180.0,
        validation_alias="OLLAMA_TIMEOUT_SECONDS",
        gt=0,
    )

    cloud_fallback_enabled: bool = Field(
        default=False,
        validation_alias="CLOUD_FALLBACK_ENABLED",
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        validation_alias="GEMINI_MODEL",
    )
    gemini_fallback_mode: Literal["ocr_text", "image"] = Field(
        default="ocr_text",
        validation_alias="GEMINI_FALLBACK_MODE",
    )

    @property
    def telegram_allowed_user_ids(self) -> set[int]:
        values = {
            int(value.strip())
            for value in self.telegram_allowed_user_ids_raw.split(",")
            if value.strip()
        }
        if not values:
            raise ValueError(
                "TELEGRAM_ALLOWED_USER_IDS must contain at least one user ID"
            )
        return values
