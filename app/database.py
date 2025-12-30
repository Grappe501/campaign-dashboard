from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine

from .config import settings


def _is_sqlite(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def _is_postgres(database_url: str) -> bool:
    return database_url.startswith("postgresql")


def _ensure_sqlite_dir(database_url: str) -> None:
    """
    Ensure the parent folder exists for SQLite file-based DB URLs like:
      sqlite:///./data/campaign.sqlite
      sqlite:////absolute/path/to/db.sqlite
    """
    if not database_url.startswith("sqlite:///"):
        return

    path = database_url.replace("sqlite:///", "", 1)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _sqlite_pragmas(engine: Engine) -> None:
    """
    Pragmas that make SQLite usable for multi-request local dev and larger datasets.
    Safe defaults that won't corrupt data.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")  # better concurrency
        cursor.execute("PRAGMA synchronous=NORMAL;")  # good perf/safety balance
        cursor.execute("PRAGMA temp_store=MEMORY;")  # speed temp ops
        cursor.execute("PRAGMA foreign_keys=ON;")  # enforce FKs
        cursor.execute("PRAGMA busy_timeout=5000;")  # reduce 'database is locked'
        # Cache size is in pages; negative means KB. e.g., -64000 = ~64MB cache
        cursor.execute("PRAGMA cache_size=-64000;")
        cursor.close()


def _postgres_session_settings(engine: Engine) -> None:
    """
    Optional: add per-connection settings for Postgres later (safe to keep now).
    Example: statement_timeout to avoid runaway queries on huge voter tables.
    """

    @event.listens_for(engine, "connect")
    def _set_postgres_settings(dbapi_connection, connection_record):
        try:
            cursor = dbapi_connection.cursor()
            # 30 seconds; tune later
            cursor.execute("SET statement_timeout = 30000;")
            cursor.close()
        except Exception:
            # Don't block startup if provider disallows it
            pass


def get_engine() -> Engine:
    """
    Create and return the SQLAlchemy engine.

    - Uses settings.resolved_database_url:
        DATABASE_URL (preferred) OR fallback DB_PATH
    - SQLite gets pragmas + check_same_thread=False for FastAPI
    - Postgres works by just changing DATABASE_URL later
    """
    database_url = settings.resolved_database_url

    if _is_sqlite(database_url):
        _ensure_sqlite_dir(database_url)

    connect_args = {"check_same_thread": False} if _is_sqlite(database_url) else {}

    engine = create_engine(
        database_url,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=True,
        # When you move to Postgres and have concurrency, you can add:
        # pool_size=5, max_overflow=10
    )

    if _is_sqlite(database_url):
        _sqlite_pragmas(engine)

    if _is_postgres(database_url):
        _postgres_session_settings(engine)

    return engine


# Single, shared engine for the app process
engine: Engine = get_engine()


def register_models() -> None:
    """
    Central place to import ALL models so SQLModel registers them.
    Prevents 'no such table' issues as the project grows.

    Keep this list current as you add features.
    """
    # Core models
    from .models.person import Person  # noqa: F401
    from .models.power_team import PowerTeam, PowerTeamMember  # noqa: F401
    from .models.voter import VoterContact  # noqa: F401
    from .models.event import Event  # noqa: F401

    # County context (current)
    from .models.county import County  # noqa: F401
    from .models.county_snapshot import CountySnapshot  # noqa: F401
    from .models.alice_county import AliceCounty  # noqa: F401

    # Power of 5 workflows
    from .models.power5_link import Power5Link  # noqa: F401
    from .models.power5_invite import Power5Invite  # noqa: F401

    # Impact Reach
    from .models.impact_rule import ImpactRule  # noqa: F401
    from .models.impact_action import ImpactAction  # noqa: F401
    from .models.impact_reach_snapshot import ImpactReachSnapshot  # noqa: F401

    # Approvals (Milestone 3 gating)
    from .models.approval_request import ApprovalRequest  # noqa: F401

    # Next milestone (uncomment when added)
    # from .models.bls_area_series import BLSAreaSeries  # noqa: F401
    # from .models.invite import Invite, InviteVerification  # noqa: F401
    # from .models.api_cache import APICache  # noqa: F401

    # Voter-file scale (future)
    # from .models.state_voter import StateVoter  # noqa: F401
    # from .models.voter_history import VoterHistory  # noqa: F401


def init_db(create_tables: bool = True) -> None:
    """
    Register models, then create missing tables (SQLite/local dev).
    Non-destructive: create_all will not drop or alter existing tables.

    When you switch to Postgres + Alembic:
      - call init_db(create_tables=False) in production startup
      - run Alembic migrations separately
    """
    register_models()
    if create_tables:
        SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """
    Simple session factory (OK for scripts).
    For FastAPI routes, prefer the yield-dependency `get_db()`.
    """
    return Session(engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency:
        def route(db: Session = Depends(get_db)):
            ...
    Ensures the session is closed after each request.
    """
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Context manager for scripts/jobs that need commit/rollback safety.

    Usage:
        with session_scope() as db:
            db.add(...)
    """
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
