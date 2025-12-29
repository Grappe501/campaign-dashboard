from __future__ import annotations

import logging
from fastapi import FastAPI
from .config import settings
from .database import init_db

from .api.people import router as people_router
from .api.teams import router as teams_router
from .api.voters import router as voters_router
from .api.events import router as events_router
from .api.external import router as external_router

def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)

    @app.get("/health")
    def health():
        return {"status": "ok", "env": settings.app_env, "app": settings.app_name}

    app.include_router(people_router)
    app.include_router(teams_router)
    app.include_router(voters_router)
    app.include_router(events_router)
    app.include_router(external_router)

    return app

app = create_app()

def run() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db()
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
