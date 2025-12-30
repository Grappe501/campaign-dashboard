from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlmodel import Session

from app.database import engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dialect_name() -> str:
    try:
        return engine.dialect.name.lower()
    except Exception:
        return "unknown"


def _sqlite_columns(session: Session, table: str) -> List[str]:
    rows = session.exec(text(f"PRAGMA table_info({table});")).all()
    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    return [str(r[1]) for r in rows]


def _postgres_columns(session: Session, table: str) -> List[str]:
    # assumes default schema 'public'
    rows = session.exec(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
            ORDER BY ordinal_position;
            """
        ),
        params={"t": table},
    ).all()
    return [str(r[0]) for r in rows]


def _get_columns(session: Session, table: str) -> List[str]:
    d = _dialect_name()
    if d == "sqlite":
        return _sqlite_columns(session, table)
    if d in ("postgresql", "postgres"):
        return _postgres_columns(session, table)
    # best effort: try sqlite PRAGMA first
    try:
        return _sqlite_columns(session, table)
    except Exception:
        return []


def _add_column_sql(table: str, col: str, coltype_sql: str, default_sql: Optional[str] = None) -> str:
    # SQLite: ALTER TABLE ... ADD COLUMN col type DEFAULT ...
    # Postgres: ALTER TABLE ... ADD COLUMN IF NOT EXISTS col type DEFAULT ...
    d = _dialect_name()
    if d in ("postgresql", "postgres"):
        stmt = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {coltype_sql}"
        if default_sql is not None:
            stmt += f" DEFAULT {default_sql}"
        stmt += ";"
        return stmt

    # sqlite
    stmt = f"ALTER TABLE {table} ADD COLUMN {col} {coltype_sql}"
    if default_sql is not None:
        stmt += f" DEFAULT {default_sql}"
    stmt += ";"
    return stmt


def _ensure_people_columns(session: Session) -> Dict[str, bool]:
    """
    Returns dict of {column_name: added?}.
    """
    table = "people"
    cols = set(_get_columns(session, table))

    added: Dict[str, bool] = {}

    # discord_user_id (string snowflake)
    if "discord_user_id" not in cols:
        session.exec(text(_add_column_sql(table, "discord_user_id", "VARCHAR", "NULL")))
        added["discord_user_id"] = True
    else:
        added["discord_user_id"] = False

    # stage_locked (bool-ish)
    # SQLite has no native boolean; 0/1 is fine. Postgres BOOLEAN.
    if "stage_locked" not in cols:
        if _dialect_name() in ("postgresql", "postgres"):
            session.exec(text(_add_column_sql(table, "stage_locked", "BOOLEAN", "FALSE")))
        else:
            session.exec(text(_add_column_sql(table, "stage_locked", "INTEGER", "0")))
        added["stage_locked"] = True
    else:
        added["stage_locked"] = False

    # stage_last_changed_at (timestamp)
    if "stage_last_changed_at" not in cols:
        if _dialect_name() in ("postgresql", "postgres"):
            session.exec(text(_add_column_sql(table, "stage_last_changed_at", "TIMESTAMPTZ", "NOW()")))
        else:
            # store ISO strings by default in SQLite; SQLModel will parse
            session.exec(text(_add_column_sql(table, "stage_last_changed_at", "TEXT", f"'{utcnow().isoformat()}'")))
        added["stage_last_changed_at"] = True
    else:
        added["stage_last_changed_at"] = False

    # stage_changed_reason (text)
    if "stage_changed_reason" not in cols:
        session.exec(text(_add_column_sql(table, "stage_changed_reason", "TEXT", "NULL")))
        added["stage_changed_reason"] = True
    else:
        added["stage_changed_reason"] = False

    return added


def _backfill_people_defaults(session: Session) -> Dict[str, int]:
    """
    Backfill defaults for rows that predate these fields.
    """
    updated: Dict[str, int] = {}

    # stage_locked defaults false/0
    try:
        if _dialect_name() in ("postgresql", "postgres"):
            r = session.exec(text("UPDATE people SET stage_locked = FALSE WHERE stage_locked IS NULL;"))
        else:
            r = session.exec(text("UPDATE people SET stage_locked = 0 WHERE stage_locked IS NULL;"))
        updated["stage_locked"] = int(getattr(r, "rowcount", 0) or 0)
    except Exception:
        updated["stage_locked"] = 0

    # stage_last_changed_at defaults to created_at when missing, else now
    try:
        if _dialect_name() in ("postgresql", "postgres"):
            r = session.exec(
                text(
                    """
                    UPDATE people
                    SET stage_last_changed_at = COALESCE(created_at, NOW())
                    WHERE stage_last_changed_at IS NULL;
                    """
                )
            )
        else:
            # SQLite: created_at may be NULL; use current ISO if so
            now_iso = utcnow().isoformat()
            r = session.exec(
                text(
                    """
                    UPDATE people
                    SET stage_last_changed_at = COALESCE(created_at, :now_iso)
                    WHERE stage_last_changed_at IS NULL;
                    """
                ),
                params={"now_iso": now_iso},
            )
        updated["stage_last_changed_at"] = int(getattr(r, "rowcount", 0) or 0)
    except Exception:
        updated["stage_last_changed_at"] = 0

    # stage_changed_reason defaults for rows missing it
    try:
        r = session.exec(
            text(
                """
                UPDATE people
                SET stage_changed_reason = 'backfill:migrate_people_stage_fields'
                WHERE stage_changed_reason IS NULL;
                """
            )
        )
        updated["stage_changed_reason"] = int(getattr(r, "rowcount", 0) or 0)
    except Exception:
        updated["stage_changed_reason"] = 0

    return updated


def run() -> None:
    """
    Run with:
      python -m app.scripts.migrate_people_stage_fields
    """
    with Session(engine) as session:
        added = _ensure_people_columns(session)
        session.commit()

        backfilled = _backfill_people_defaults(session)
        session.commit()

    print("✅ migrate_people_stage_fields complete")
    print("Added columns:", added)
    print("Backfilled rows:", backfilled)


if __name__ == "__main__":
    run()
