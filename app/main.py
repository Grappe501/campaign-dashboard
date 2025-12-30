from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db

# Existing routers
from .api.people import router as people_router
from .api.teams import router as teams_router
from .api.voters import router as voters_router
from .api.events import router as events_router
from .api.external import router as external_router
from .api.counties import router as counties_router

# Milestone 3 routers (Power of 5 + Impact Reach + Bootstrap + Approvals)
from .api.power5 import router as power5_router
from .api.impact import router as impact_router
from .api.bootstrap import router as bootstrap_router
from .api.approvals import router as approvals_router

# Future routers (placeholders — add when ready)
# from .api.auth import router as auth_router          # magic link onboarding, sessions
# from .api.pipeline import router as pipeline_router  # voter pipeline skeleton
# from .api.replication import router as repl_router   # event replication
# from .api.discord import router as discord_router    # discord webhooks / admin ops


def create_app() -> FastAPI:
    app = FastAPI(
        title="Campaign Dashboard API",
        version=getattr(settings, "app_version", "0.3.x"),
    )

    # --- CORS ---
    # For local dev + Discord bot (server-to-server calls don’t require CORS, but UI might)
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

    # --- Health / meta ---
    @app.get("/health", tags=["meta"])
    def health():
        return {"ok": True, "env": getattr(settings, "env", "local")}

    @app.get("/version", tags=["meta"])
    def version():
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
    # app.include_router(auth_router)
    # app.include_router(pipeline_router)
    # app.include_router(repl_router)
    # app.include_router(discord_router)

    return app


app = create_app()


def run() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    import uvicorn

    # NOTE: init_db is handled by the FastAPI startup hook.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
