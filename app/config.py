from __future__ import annotations

from pathlib import Path
from typing import Any, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_origins(raw: Any) -> List[str]:
    """
    Normalize CORS allow origins from env.

    Supports:
      - list[str] (already parsed)
      - "*" or "['*']"
      - comma-separated string: "https://a.com, https://b.com"
    """
    if raw is None:
        return ["*"]

    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
        items = [x for x in items if x]
        return items or ["*"]

    s = str(raw).strip()
    if not s:
        return ["*"]

    # Common "single star" case
    if s == "*":
        return ["*"]

    # Comma-separated origins
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
        parts = [p for p in parts if p]
        return parts or ["*"]

    return [s]


class Settings(BaseSettings):
    """
    Central app settings (backend).

    Phase 5.2 hardening goals:
    - Keep backwards compatibility with env var names
    - Normalize user-provided values (CORS, log level, DB URL)
    - Provide a single resolved DB URL source of truth
    - Remain permissive for local dev, safe for hosted use
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App identity
    env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="campaign-dashboard", alias="APP_NAME")
    # Default to current package zip version you referenced
    app_version: str = Field(default="0.4.0", alias="APP_VERSION")
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

    # SQLite schema safety (local dev)
    # If True and using SQLite, we will attempt non-destructive ALTER TABLEs
    # for missing columns during init_db(). Keep False in production DBs.
    sqlite_auto_migrate: bool = Field(default=True, alias="SQLITE_AUTO_MIGRATE")

    # Secrets / keys
    discord_bot_token: str = Field(default="", alias="DISCORD_BOT_TOKEN")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    census_api_key: str = Field(default="", alias="CENSUS_API_KEY")
    bls_api_key: str = Field(default="", alias="BLS_API_KEY")

    # Optional: if you deploy publicly, set this so /health can report it
    public_api_base: str = Field(default="", alias="PUBLIC_API_BASE")

    # -------------------------
    # Validators / normalizers
    # -------------------------

    @field_validator("log_level", mode="before")
    @classmethod
    def _norm_log_level(cls, v: Any) -> str:
        s = ("" if v is None else str(v)).strip().upper()
        return s or "INFO"

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _norm_cors_allow_origins(cls, v: Any) -> list[str]:
        return _split_origins(v)

    @field_validator("host", mode="before")
    @classmethod
    def _norm_host(cls, v: Any) -> str:
        s = ("" if v is None else str(v)).strip()
        return s or "127.0.0.1"

    @field_validator("public_api_base", mode="before")
    @classmethod
    def _norm_public_api_base(cls, v: Any) -> str:
        s = ("" if v is None else str(v)).strip().rstrip("/")
        return s

    @field_validator("database_url", mode="before")
    @classmethod
    def _norm_database_url(cls, v: Any) -> str:
        s = ("" if v is None else str(v)).strip()
        return s

    @field_validator("db_path", mode="before")
    @classmethod
    def _norm_db_path(cls, v: Any) -> str:
        s = ("" if v is None else str(v)).strip()
        return s or "./data/campaign.sqlite"

    # -------------------------
    # Derived helpers
    # -------------------------

    @property
    def is_prod(self) -> bool:
        return str(self.env).strip().lower() in ("prod", "production")

    @property
    def resolved_database_url(self) -> str:
        """
        Priority:
        1) DATABASE_URL if provided
        2) Build sqlite:/// URL from DB_PATH

        Accepts:
          - DB_PATH can be full sqlite URL ("sqlite:///./data/x.sqlite" or "sqlite:////abs/path")
          - Or file path ("./data/x.sqlite", "data/x.sqlite", "/abs/path/x.sqlite")
        """
        if self.database_url:
            return self.database_url

        path = (self.db_path or "").strip()
        if not path:
            path = "./data/campaign.sqlite"

        # If already a URL, accept it
        if path.startswith("sqlite:"):
            return path

        # Treat as filesystem path
        p = Path(path)

        # If relative, anchor to cwd with ./ prefix for sqlite URL consistency
        if not p.is_absolute():
            # preserve user intent if they already used "./"
            if str(p).startswith("./"):
                return f"sqlite:///{p.as_posix()}"
            return f"sqlite:///./{p.as_posix()}"

        # Absolute path needs 4 slashes after scheme (sqlite:////abs/path)
        return f"sqlite:////{p.as_posix().lstrip('/')}"


settings = Settings()
