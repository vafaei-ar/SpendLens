from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_ids_raw: str = Field(validation_alias="TELEGRAM_ALLOWED_USER_IDS")
    data_dir: Path = Field(default=Path("data"), validation_alias="SPENDLENS_DATA_DIR")
    max_source_bytes: int = Field(
        default=25 * 1024 * 1024,
        validation_alias="SPENDLENS_MAX_SOURCE_BYTES",
        ge=1,
    )

    @property
    def telegram_allowed_user_ids(self) -> set[int]:
        values = {
            int(value.strip())
            for value in self.telegram_allowed_user_ids_raw.split(",")
            if value.strip()
        }
        if not values:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one user ID")
        return values
