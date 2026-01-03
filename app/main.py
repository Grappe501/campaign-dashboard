from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
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


def _safe_settings_snapshot() -> Dict[str, Any]:
    """
    Non-secret runtime snapshot for operators.
    Keep this stable and safe (no tokens, no keys).
    """
    return {
        "env": getattr(settings, "env", "local"),
        "app_version": getattr(settings, "app_version", "0.3.x"),
        "host": getattr(settings, "host", "127.0.0.1"),
        "port": int(getattr(settings, "port", 8000)),
        "reload": bool(getattr(settings, "reload", False)),
        "cors_allow_origins": getattr(settings, "cors_allow_origins", ["*"]),
        "public_api_base": getattr(settings, "public_api_base", ""),
    }


def _db_ok() -> bool:
    """
    Lightweight DB connectivity check for readiness.

    IMPORTANT: uses the shared process engine (no accidental new engine creation).
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
        version=getattr(settings, "app_version", "0.3.x"),
    )

    # --- CORS ---
    # For local dev + any hosted UI; Discord bot (server-to-server) doesn't need CORS.
    allow_origins = getattr(settings, "cors_allow_origins", ["*"])
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
        # Creates tables for all registered SQLModel models (idempotent for SQLite)
        init_db()
        logger.info("API startup complete (version=%s)", getattr(settings, "app_version", "0.3.x"))

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
        return {"version": getattr(settings, "app_version", "0.3.x")}

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
    logging.basicConfig(level=getattr(logging, str(settings.log_level).upper(), logging.INFO))
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=getattr(settings, "host", "127.0.0.1"),
        port=int(getattr(settings, "port", 8000)),
        reload=bool(getattr(settings, "reload", False)),
    )
