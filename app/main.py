from __future__ import annotations

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db

from .api.people import router as people_router
from .api.teams import router as teams_router
from .api.voters import router as voters_router
from .api.events import router as events_router
from .api.external import router as external_router
from .api.counties import router as counties_router


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        # Future-friendly: shows env in the docs title area if you want to extend later
        version="0.2.0",
    )

    # ✅ Ensure DB tables exist on every startup (prevents "no such table" in dev)
    @app.on_event("startup")
    def _startup() -> None:
        # Later, when you move to Postgres + Alembic:
        # init_db(create_tables=False)
        init_db()

    # ✅ CORS (safe default for local dashboard/front-end)
    # Later you can tighten origins to your deployed UI domain(s).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "env": settings.app_env,
            "app": settings.app_name,
        }

    @app.get("/")
    def root():
        # Nice for browsers and load balancers; avoids noisy 404s
        return {
            "app": settings.app_name,
            "env": settings.app_env,
            "health": "/health",
            "docs": "/docs",
        }

    # API routers
    app.include_router(people_router)
    app.include_router(teams_router)
    app.include_router(voters_router)
    app.include_router(events_router)
    app.include_router(external_router)
    app.include_router(counties_router)

    return app


app = create_app()


def run() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    import uvicorn

    # NOTE: init_db is handled by the FastAPI startup hook now.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
