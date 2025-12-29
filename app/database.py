from __future__ import annotations

import os
from sqlmodel import SQLModel, create_engine, Session
from .config import settings

def _ensure_db_dir() -> None:
    db_path = settings.db_path
    if db_path.startswith("sqlite:///"):
        db_path = db_path.replace("sqlite:///", "", 1)
    if db_path.startswith("./") or db_path.startswith("../") or db_path.startswith("/"):
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

def get_engine():
    _ensure_db_dir()
    # SQLModel expects SQLAlchemy URL
    url = f"sqlite:///{settings.db_path.lstrip('./')}" if settings.db_path.startswith("./") else f"sqlite:///{settings.db_path}"
    return create_engine(url, echo=False, connect_args={"check_same_thread": False})

engine = get_engine()

def init_db() -> None:
    from .models.person import Person
    from .models.power_team import PowerTeam, PowerTeamMember
    from .models.voter import VoterContact
    from .models.event import Event
    SQLModel.metadata.create_all(engine)

def get_session() -> Session:
    return Session(engine)
