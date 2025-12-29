from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="campaign-dashboard", alias="APP_NAME")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    db_path: str = Field(default="./data/campaign.sqlite", alias="DB_PATH")

    # Secrets (local only)
    discord_bot_token: str = Field(default="", alias="DISCORD_BOT_TOKEN")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    census_api_key: str = Field(default="", alias="CENSUS_API_KEY")
    bls_api_key: str = Field(default="", alias="BLS_API_KEY")

settings = Settings()
