from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central app settings.

    Milestone 3 goals supported here:
    - reduce "only I can see it" confusion by making host/port/reload explicit settings
    - provide a single place to define CORS allow origins for any hosted UI
    - keep backwards compatibility with existing env var names
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App identity
    env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="campaign-dashboard", alias="APP_NAME")
    app_version: str = Field(default="0.3.x", alias="APP_VERSION")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Server runtime (uvicorn)
    host: str = Field(default="127.0.0.1", alias="HOST")  # set 0.0.0.0 for LAN / container
    port: int = Field(default=8000, alias="PORT")
    reload: bool = Field(default=False, alias="RELOAD")

    # CORS (UI only; bot doesn't need it)
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"], alias="CORS_ALLOW_ORIGINS")

    # Preferred: a real SQLAlchemy URL (SQLite now, Postgres later)
    database_url: str = Field(default="", alias="DATABASE_URL")

    # Back-compat: allow old DB_PATH if DATABASE_URL not set
    db_path: str = Field(default="./data/campaign.sqlite", alias="DB_PATH")

    # Secrets
    discord_bot_token: str = Field(default="", alias="DISCORD_BOT_TOKEN")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    census_api_key: str = Field(default="", alias="CENSUS_API_KEY")
    bls_api_key: str = Field(default="", alias="BLS_API_KEY")

    # Optional: if you deploy publicly, set this so /health can report it
    public_api_base: str = Field(default="", alias="PUBLIC_API_BASE")

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
