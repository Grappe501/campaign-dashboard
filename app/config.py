from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="campaign-dashboard", alias="APP_NAME")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Preferred: a real SQLAlchemy URL (SQLite now, Postgres later)
    database_url: str = Field(default="", alias="DATABASE_URL")

    # Back-compat: allow old DB_PATH if DATABASE_URL not set
    db_path: str = Field(default="./data/campaign.sqlite", alias="DB_PATH")

    # Secrets (local only)
    discord_bot_token: str = Field(default="", alias="DISCORD_BOT_TOKEN")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    census_api_key: str = Field(default="", alias="CENSUS_API_KEY")
    bls_api_key: str = Field(default="", alias="BLS_API_KEY")

    @property
    def resolved_database_url(self) -> str:
        """
        Priority:
        1) DATABASE_URL if provided
        2) Build sqlite:/// URL from DB_PATH
        """
        if self.database_url:
            return self.database_url

        # DB_PATH might be "./data/campaign.sqlite" or "data/campaign.sqlite"
        path = self.db_path
        if path.startswith("sqlite:"):
            # user already gave a full sqlite URL in DB_PATH
            return path
        if path.startswith("./"):
            return f"sqlite:///{path}"
        # treat as relative file path
        return f"sqlite:///./{path}"

settings = Settings()
