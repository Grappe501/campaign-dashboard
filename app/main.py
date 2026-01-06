from __future__ import annotations

import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# IMPORTANT:
# Do NOT import `settings` via `from .config import settings` because `app/config.py`
# intentionally allows `app.config.settings` (bot settings) to coexist, which can
# shadow/override imports.
#
# Backend should always use env vars directly (or a dedicated backend module),
# never the bot settings module.
from .database import db_runtime_snapshot, engine, init_db

# Existing routers
from .api.people import router as people_router
from .api.teams import router as teams_router
from .api.voters import router as voters_router
from .api.events import router as events_router
from .api.external import router as external_router
from .api.counties import router as counties_router

# Milestone 3 routers
from .api.power5 import router as power5_router
from .api.impact import router as impact_router
from .api.bootstrap import router as bootstrap_router
from .api.approvals import router as approvals_router

# Milestone 4 routers
from .api.training import router as training_router

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _safe_settings_snapshot() -> Dict[str, Any]:
    """
    Non-secret runtime snapshot for operators.
    Keep stable and safe (no tokens/keys).
    """
    cors_raw = _env("CORS_ALLOW_ORIGINS", "*").strip()
    return {
        "env": _env("APP_ENV", "local").strip() or "local",
        "app_version": _env("APP_VERSION", "0.4.0").strip() or "0.4.0",
        "host": _env("HOST", "127.0.0.1").strip() or "127.0.0.1",
        "port": _env_int("PORT", 8000),
        "reload": _env_bool("RELOAD", False),
        "cors_allow_origins": cors_raw,
        "public_api_base": _env("PUBLIC_API_BASE", "").strip(),
    }


def _db_ok() -> bool:
    """
    Lightweight DB connectivity check for readiness.

    IMPORTANT: uses the shared process engine.
    """
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        logger.exception("DB health check failed")
        return False


def create_app() -> FastAPI:
    app = FastAPI(
        title="Campaign Dashboard API",
        version=_env("APP_VERSION", "0.4.0").strip() or "0.4.0",
    )

    # --- CORS ---
    # For local dev + any hosted UI; Discord bot (server-to-server) doesn't need CORS.
    # Keep permissive by default for local.
    cors_allow = _env("CORS_ALLOW_ORIGINS", "*").strip() or "*"
    allow_origins = ["*"] if cors_allow == "*" else [o.strip() for o in cors_allow.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Startup ---
    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        logger.info("API startup complete (version=%s)", _env("APP_VERSION", "0.4.0").strip() or "0.4.0")

    # --- Friendly error envelope (API callers + bot) ---
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail: Any
        try:
            detail = exc.detail
        except Exception:
            detail = "HTTP error"
        return JSONResponse(status_code=getattr(exc, "status_code", 500), content={"detail": detail})

    # --- Health / readiness / meta ---
    @app.get("/health", tags=["meta"])
    def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "db_ok": _db_ok(),
            **_safe_settings_snapshot(),
            **db_runtime_snapshot(),
        }

    @app.get("/ready", tags=["meta"])
    def ready() -> Dict[str, Any]:
        if not _db_ok():
            raise HTTPException(status_code=503, detail="Database not ready")
        return {"ready": True}

    @app.get("/meta", tags=["meta"])
    def meta() -> Dict[str, Any]:
        return {
            **_safe_settings_snapshot(),
            **db_runtime_snapshot(),
        }

    @app.get("/version", tags=["meta"])
    def version() -> Dict[str, Any]:
        return {"version": _env("APP_VERSION", "0.4.0").strip() or "0.4.0"}

    # --- API routers ---
    app.include_router(people_router)
    app.include_router(teams_router)
    app.include_router(voters_router)
    app.include_router(events_router)
    app.include_router(external_router)
    app.include_router(counties_router)

    # Milestone 3: Power of 5 + Impact Reach + Bootstrap + Approvals
    app.include_router(power5_router)
    app.include_router(impact_router)
    app.include_router(bootstrap_router)
    app.include_router(approvals_router)

    # Milestone 4: Training / SOP system
    app.include_router(training_router)

    return app


app = create_app()


def run() -> None:
    # Use LOG_LEVEL from env; default INFO.
    level_name = (_env("LOG_LEVEL", "INFO").strip() or "INFO").upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO))

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=_env("HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_env_int("PORT", 8000),
        reload=_env_bool("RELOAD", False),
    )
