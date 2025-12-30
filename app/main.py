from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .database import init_db

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

# Optional Discord router (if present later)
# from .api.discord import router as discord_router


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

    # --- Friendly error envelope (API callers + bot) ---
    @app.exception_handler(HTTPException)  # type: ignore[name-defined]
    async def http_exception_handler(request, exc):  # noqa: ANN001
        # Keep FastAPI semantics but provide a consistent JSON structure
        try:
            detail = exc.detail
        except Exception:
            detail = "HTTP error"
        return JSONResponse(status_code=getattr(exc, "status_code", 500), content={"detail": detail})

    # --- Health / meta ---
    @app.get("/health", tags=["meta"])
    def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "env": getattr(settings, "env", "local"),
            "api_base": os.getenv("PUBLIC_API_BASE", ""),
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

    # Future milestones (uncomment when the modules exist)
    # app.include_router(discord_router)

    return app


app = create_app()


def run() -> None:
    logging.basicConfig(level=getattr(logging, str(settings.log_level).upper(), logging.INFO))
    import uvicorn

    # NOTE: init_db is handled by the FastAPI startup hook.
    # reload should be True in local dev, False in prod.
    uvicorn.run(
        "app.main:app",
        host=getattr(settings, "host", "127.0.0.1"),
        port=int(getattr(settings, "port", 8000)),
        reload=bool(getattr(settings, "reload", False)),
    )
