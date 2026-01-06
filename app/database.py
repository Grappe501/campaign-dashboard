from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List, Optional

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _resolved_database_url() -> str:
    """
    Resolve DB URL without importing app.config.

    Why:
    - app/config.py intentionally "acts like a package" so `app.config.settings`
      exists for the Discord bot.
    - That makes `from app.config import settings` ambiguous and can import the
      bot settings module instead of the backend Settings() instance.
    - DB must be unambiguous and always boot.

    Priority:
      1) DATABASE_URL if provided
      2) Build sqlite:/// URL from DB_PATH
    """
    database_url = _env("DATABASE_URL", "").strip()
    if database_url:
        return database_url

    path = _env("DB_PATH", "./data/campaign.sqlite").strip() or "./data/campaign.sqlite"

    # If already a sqlite URL, accept it
    if path.startswith("sqlite:"):
        return path

    p = Path(path)

    # If relative, anchor to cwd with ./ prefix for sqlite URL consistency
    if not p.is_absolute():
        if str(p).startswith("./"):
            return f"sqlite:///{p.as_posix()}"
        return f"sqlite:///./{p.as_posix()}"

    # Absolute path needs 4 slashes after scheme (sqlite:////abs/path)
    return f"sqlite:////{p.as_posix().lstrip('/')}"


def _is_sqlite(database_url: str) -> bool:
    return (database_url or "").startswith("sqlite")


def _is_postgres(database_url: str) -> bool:
    return (database_url or "").startswith("postgresql")


def _sqlite_file_path_from_url(database_url: str) -> Optional[str]:
    """
    Extract a filesystem path from common SQLite URL forms.

    Supports:
      - sqlite:///./data/campaign.sqlite
      - sqlite:////absolute/path/to/db.sqlite

    Returns None for:
      - sqlite:// (in-memory / invalid forms)
      - non-sqlite URLs
    """
    if not _is_sqlite(database_url):
        return None

    s = (database_url or "").strip()

    # Absolute path form
    if s.startswith("sqlite:////"):
        # sqlite:////abs/path -> /abs/path
        return "/" + s[len("sqlite:////") :]

    # Relative path form
    if s.startswith("sqlite:///"):
        return s[len("sqlite:///") :]

    # Other sqlite forms (e.g., sqlite://, sqlite:///:memory:) — ignore
    return None


def _ensure_sqlite_dir(database_url: str) -> None:
    """
    Ensure the parent folder exists for SQLite file-based DB URLs like:
      sqlite:///./data/campaign.sqlite
      sqlite:////absolute/path/to/db.sqlite
    """
    path = _sqlite_file_path_from_url(database_url)
    if not path:
        return

    # If it's :memory: or otherwise non-file, do nothing
    if path.strip() in (":memory:", ""):
        return

    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _sqlite_pragmas(engine: Engine) -> None:
    """
    Pragmas that make SQLite usable for multi-request local dev and larger datasets.
    Safe defaults that won't corrupt data.

    NOTE: This attaches a SQLAlchemy 'connect' event listener to the engine.
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
            cursor.execute("SET statement_timeout = 30000;")
            cursor.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# Engine lifecycle (single shared engine per process)
# ---------------------------------------------------------------------

_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    """
    Return the single shared SQLAlchemy engine for this process.
    """
    global _ENGINE

    if _ENGINE is not None:
        return _ENGINE

    database_url = str(_resolved_database_url() or "").strip()
    if not database_url:
        raise RuntimeError("Resolved DATABASE_URL is empty. Check DATABASE_URL or DB_PATH in .env.")

    if _is_sqlite(database_url):
        _ensure_sqlite_dir(database_url)

    connect_args = {"check_same_thread": False} if _is_sqlite(database_url) else {}

    engine = create_engine(
        database_url,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=True,
    )

    if _is_sqlite(database_url):
        _sqlite_pragmas(engine)

    if _is_postgres(database_url):
        _postgres_session_settings(engine)

    _ENGINE = engine
    return _ENGINE


engine: Engine = get_engine()


def db_runtime_snapshot() -> dict:
    """
    Non-secret snapshot useful for operators and /meta style endpoints.
    """
    return {
        "resolved_database_url": _resolved_database_url(),
        "sqlite_auto_migrate": bool(_env_bool("SQLITE_AUTO_MIGRATE", True)),
    }


# ---------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------


def register_models() -> None:
    """
    Central place to import ALL models so SQLModel registers them.
    """
    from . import models as _models  # noqa: F401


# ---------------------------------------------------------------------
# SQLite micro-migrations (ADD COLUMN only)
# ---------------------------------------------------------------------


def _sqlite_table_exists(session: Session, table: str) -> bool:
    stmt = text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name").bindparams(name=table)
    row = session.exec(stmt).first()
    return bool(row)


def _sqlite_get_columns(session: Session, table: str) -> List[str]:
    cols: List[str] = []
    rows = session.exec(text(f"PRAGMA table_info({table});")).all()
    for r in rows:
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
    Conservative: only ADD COLUMN and optional backfills.

    Controlled by env SQLITE_AUTO_MIGRATE.
    """
    if not bool(_env_bool("SQLITE_AUTO_MIGRATE", True)):
        return

    database_url = _resolved_database_url()
    if not _is_sqlite(database_url):
        return

    with Session(engine) as session:
        # -----------------------------------------------------------------
        # people  (root cause of your 500s was missing people.zip_code)
        # -----------------------------------------------------------------
        _sqlite_add_column_if_missing(session, table="people", column="email", ddl="email TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="phone", ddl="phone TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="discord_user_id", ddl="discord_user_id TEXT")

        _sqlite_add_column_if_missing(session, table="people", column="onboarded_at", ddl="onboarded_at DATETIME")

        _sqlite_add_column_if_missing(session, table="people", column="stage", ddl="stage TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="stage_locked", ddl="stage_locked BOOLEAN DEFAULT 0")
        _sqlite_add_column_if_missing(
            session, table="people", column="stage_last_changed_at", ddl="stage_last_changed_at DATETIME"
        )
        _sqlite_add_column_if_missing(session, table="people", column="stage_changed_reason", ddl="stage_changed_reason TEXT")

        # Geo placement
        _sqlite_add_column_if_missing(session, table="people", column="zip_code", ddl="zip_code TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="region", ddl="region TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="county", ddl="county TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="city", ddl="city TEXT")
        _sqlite_add_column_if_missing(session, table="people", column="precinct", ddl="precinct TEXT")

        _sqlite_add_column_if_missing(
            session, table="people", column="recruited_by_person_id", ddl="recruited_by_person_id INTEGER"
        )

        # Consent flags
        _sqlite_add_column_if_missing(session, table="people", column="allow_tracking", ddl="allow_tracking BOOLEAN DEFAULT 1")
        _sqlite_add_column_if_missing(
            session, table="people", column="allow_discord_comms", ddl="allow_discord_comms BOOLEAN DEFAULT 1"
        )
        _sqlite_add_column_if_missing(
            session, table="people", column="allow_leaderboard", ddl="allow_leaderboard BOOLEAN DEFAULT 1"
        )

        # Access flags
        _sqlite_add_column_if_missing(session, table="people", column="team_access", ddl="team_access BOOLEAN DEFAULT 0")
        _sqlite_add_column_if_missing(
            session, table="people", column="fundraising_access", ddl="fundraising_access BOOLEAN DEFAULT 0"
        )
        _sqlite_add_column_if_missing(session, table="people", column="leader_access", ddl="leader_access BOOLEAN DEFAULT 0")
        _sqlite_add_column_if_missing(session, table="people", column="is_admin", ddl="is_admin BOOLEAN DEFAULT 0")

        # Discord audit
        _sqlite_add_column_if_missing(
            session, table="people", column="last_seen_discord_guild_id", ddl="last_seen_discord_guild_id TEXT"
        )
        _sqlite_add_column_if_missing(
            session, table="people", column="last_seen_discord_channel_id", ddl="last_seen_discord_channel_id TEXT"
        )
        _sqlite_add_column_if_missing(
            session, table="people", column="last_seen_discord_username", ddl="last_seen_discord_username TEXT"
        )

        # Timestamps
        _sqlite_add_column_if_missing(session, table="people", column="created_at", ddl="created_at DATETIME")
        _sqlite_add_column_if_missing(session, table="people", column="updated_at", ddl="updated_at DATETIME")

        # -----------------------------------------------------------------
        # power_teams
        # -----------------------------------------------------------------
        _sqlite_add_column_if_missing(session, table="power_teams", column="name", ddl="name TEXT")
        _sqlite_add_column_if_missing(session, table="power_teams", column="min_goal_size", ddl="min_goal_size INTEGER")
        _sqlite_add_column_if_missing(session, table="power_teams", column="created_at", ddl="created_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power_teams", column="updated_at", ddl="updated_at DATETIME")

        # -----------------------------------------------------------------
        # power_team_members
        # -----------------------------------------------------------------
        _sqlite_add_column_if_missing(session, table="power_team_members", column="person_id", ddl="person_id INTEGER")
        _sqlite_backfill_if_possible(session, table="power_team_members", dst_col="person_id", src_col="member_person_id")
        _sqlite_add_column_if_missing(session, table="power_team_members", column="joined_at", ddl="joined_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power_team_members", column="updated_at", ddl="updated_at DATETIME")

        # -----------------------------------------------------------------
        # power5_invites
        # -----------------------------------------------------------------
        _sqlite_add_column_if_missing(
            session, table="power5_invites", column="invitee_person_id", ddl="invitee_person_id INTEGER"
        )
        _sqlite_add_column_if_missing(session, table="power5_invites", column="channel", ddl="channel TEXT")
        _sqlite_add_column_if_missing(session, table="power5_invites", column="destination", ddl="destination TEXT")
        _sqlite_add_column_if_missing(session, table="power5_invites", column="token_hash", ddl="token_hash TEXT")
        _sqlite_add_column_if_missing(session, table="power5_invites", column="expires_at", ddl="expires_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power5_invites", column="consumed_at", ddl="consumed_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power5_invites", column="created_at", ddl="created_at DATETIME")

        # -----------------------------------------------------------------
        # power5_links
        # -----------------------------------------------------------------
        _sqlite_add_column_if_missing(session, table="power5_links", column="depth", ddl="depth INTEGER")
        _sqlite_add_column_if_missing(session, table="power5_links", column="status", ddl="status TEXT")
        _sqlite_add_column_if_missing(session, table="power5_links", column="invited_at", ddl="invited_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power5_links", column="onboarded_at", ddl="onboarded_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power5_links", column="activated_at", ddl="activated_at DATETIME")
        _sqlite_add_column_if_missing(session, table="power5_links", column="created_at", ddl="created_at DATETIME")

        # -----------------------------------------------------------------
        # voter_contacts
        # -----------------------------------------------------------------
        _sqlite_add_column_if_missing(session, table="voter_contacts", column="updated_at", ddl="updated_at DATETIME")


# ---------------------------------------------------------------------
# Public init + session helpers
# ---------------------------------------------------------------------


def init_db(create_tables: bool = True) -> None:
    """
    Register models, then create missing tables (SQLite/local dev).
    create_all is non-destructive (won't alter existing tables),
    so we also run conservative SQLite auto-migrations when enabled.
    """
    register_models()
    if create_tables:
        SQLModel.metadata.create_all(engine)
    _sqlite_auto_migrate()


def get_session() -> Session:
    """
    Simple session factory (OK for scripts).
    """
    return Session(engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency session.
    """
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Context manager for scripts/jobs that need commit/rollback safety.
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
