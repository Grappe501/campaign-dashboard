from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator, List, Optional

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine

from .config import settings


def _is_sqlite(database_url: str) -> bool:
    return (database_url or "").startswith("sqlite")


def _is_postgres(database_url: str) -> bool:
    return (database_url or "").startswith("postgresql")


def _ensure_sqlite_dir(database_url: str) -> None:
    """
    Ensure the parent folder exists for SQLite file-based DB URLs like:
      sqlite:///./data/campaign.sqlite
      sqlite:////absolute/path/to/db.sqlite
    """
    if not (database_url or "").startswith("sqlite:///"):
        return

    path = database_url.replace("sqlite:///", "", 1)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _sqlite_pragmas(engine: Engine) -> None:
    """
    Pragmas that make SQLite usable for multi-request local dev and larger datasets.
    Safe defaults that won't corrupt data.

    NOTE: This function attaches a SQLAlchemy 'connect' event listener to the engine.
    It must be called exactly once per engine instance.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
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
    def _set_postgres_settings(dbapi_connection, connection_record):  # noqa: ANN001
        try:
            cursor = dbapi_connection.cursor()
            # 30 seconds; tune later
            cursor.execute("SET statement_timeout = 30000;")
            cursor.close()
        except Exception:
            # Don't block startup if provider disallows it
            pass


# ---------------------------------------------------------------------
# Engine lifecycle (single shared engine per process)
# ---------------------------------------------------------------------

_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    """
    Return the single shared SQLAlchemy engine for this process.

    Operator Readiness:
    - This must NOT create a new engine each call.
    - Health checks should use this shared engine so pragmas/pools are consistent.

    Uses settings.resolved_database_url:
      DATABASE_URL (preferred) OR fallback DB_PATH
    """
    global _ENGINE

    if _ENGINE is not None:
        return _ENGINE

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

    _ENGINE = engine
    return _ENGINE


# Public shared engine (backwards compatible import sites)
engine: Engine = get_engine()


def db_runtime_snapshot() -> dict:
    """
    Non-secret snapshot useful for operators and /meta style endpoints.
    """
    return {
        "resolved_database_url": getattr(settings, "resolved_database_url", ""),
        "sqlite_auto_migrate": bool(getattr(settings, "sqlite_auto_migrate", False)),
    }


# ---------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------

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

    # County context
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

    # Milestone 4: Training / SOP
    from .models.training_module import TrainingModule  # noqa: F401
    from .models.training_completion import TrainingCompletion  # noqa: F401


# ---------------------------------------------------------------------
# SQLite micro-migrations (ADD COLUMN only)
# ---------------------------------------------------------------------

def _sqlite_table_exists(session: Session, table: str) -> bool:
    row = session.exec(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        params={"name": table},
    ).first()
    return bool(row)


def _sqlite_get_columns(session: Session, table: str) -> List[str]:
    cols: List[str] = []
    rows = session.exec(text(f"PRAGMA table_info({table});")).all()
    for r in rows:
        # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
        try:
            cols.append(str(r[1]))
        except Exception:
            continue
    return cols


def _sqlite_add_column_if_missing(
    session: Session,
    *,
    table: str,
    column: str,
    ddl: str,
) -> bool:
    """
    Non-destructive SQLite-only micro-migration.
    Returns True if column was added.
    """
    if not _sqlite_table_exists(session, table):
        return False

    existing = set(_sqlite_get_columns(session, table))
    if column in existing:
        return False

    session.exec(text(f"ALTER TABLE {table} ADD COLUMN {ddl};"))
    session.commit()
    return True


def _sqlite_backfill_if_possible(
    session: Session,
    *,
    table: str,
    dst_col: str,
    src_col: str,
) -> None:
    """
    Backfill dst_col from src_col if both columns exist.
    Safe no-op if table/columns are missing.
    """
    if not _sqlite_table_exists(session, table):
        return

    existing = set(_sqlite_get_columns(session, table))
    if dst_col not in existing or src_col not in existing:
        return

    session.exec(
        text(
            f"""
            UPDATE {table}
            SET {dst_col} = {src_col}
            WHERE ({dst_col} IS NULL OR {dst_col} = 0)
              AND {src_col} IS NOT NULL
            """
        )
    )
    session.commit()


def _sqlite_auto_migrate() -> None:
    """
    SQLite-only, local-dev convenience to add missing columns in-place.

    This is intentionally conservative:
    - only ADD COLUMN operations
    - optional backfills (no drops/renames)
    - gated behind settings.sqlite_auto_migrate
    """
    if not getattr(settings, "sqlite_auto_migrate", False):
        return

    database_url = settings.resolved_database_url
    if not _is_sqlite(database_url):
        return

    with Session(engine) as session:
        # ---- people ----
        # New timestamp column (from Person model hardening)
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="updated_at",
            ddl="updated_at DATETIME",
        )

        # Canonical access booleans (if DB was created before these fields existed)
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="team_access",
            ddl="team_access BOOLEAN DEFAULT 0",
        )
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="fundraising_access",
            ddl="fundraising_access BOOLEAN DEFAULT 0",
        )
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="leader_access",
            ddl="leader_access BOOLEAN DEFAULT 0",
        )

        # Discord last-seen fields (forward compatible; harmless if already present)
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="last_seen_discord_guild_id",
            ddl="last_seen_discord_guild_id TEXT",
        )
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="last_seen_discord_channel_id",
            ddl="last_seen_discord_channel_id TEXT",
        )
        _sqlite_add_column_if_missing(
            session,
            table="people",
            column="last_seen_discord_username",
            ddl="last_seen_discord_username TEXT",
        )

        # ---- power_team_members ----
        # Standardize member FK to person_id (backfill from prior column if it exists)
        _sqlite_add_column_if_missing(
            session,
            table="power_team_members",
            column="person_id",
            ddl="person_id INTEGER",
        )
        _sqlite_backfill_if_possible(
            session,
            table="power_team_members",
            dst_col="person_id",
            src_col="member_person_id",
        )

        # ---- power5_invites ----
        _sqlite_add_column_if_missing(
            session,
            table="power5_invites",
            column="invitee_person_id",
            ddl="invitee_person_id INTEGER",
        )

        # ---- voter_contacts ----
        # If older DB lacked updated_at
        _sqlite_add_column_if_missing(
            session,
            table="voter_contacts",
            column="updated_at",
            ddl="updated_at DATETIME",
        )


# ---------------------------------------------------------------------
# Public init + session helpers
# ---------------------------------------------------------------------

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

    # SQLite-only convenience migrations
    _sqlite_auto_migrate()


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
