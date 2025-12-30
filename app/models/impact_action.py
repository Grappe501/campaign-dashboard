from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActionSource(str, Enum):
    DISCORD = "discord"
    WEB = "web"
    IMPORT = "import"
    ADMIN = "admin"
    API = "api"
    UNKNOWN = "unknown"


class ActionChannel(str, Enum):
    CALL = "call"
    TEXT = "text"
    DOOR = "door"
    EVENT = "event"
    SOCIAL = "social"
    OTHER = "other"


class ImpactAction(SQLModel, table=True):
    """
    Ledger of real activity: calls, texts, doors, posts, events, etc.

    Option B (recommended long-term):
      - Uses structured JSON `meta` instead of `meta_json`
      - Adds idempotency_key for dedupe across retries / double-submits
      - Adds source/channel for reporting
      - Uses timezone-aware UTC timestamps
    """

    __tablename__ = "impact_actions"

    id: Optional[int] = Field(default=None, primary_key=True)

    actor_person_id: Optional[int] = Field(default=None, foreign_key="people.id", index=True)
    county_id: Optional[int] = Field(default=None, foreign_key="counties.id", index=True)
    power_team_id: Optional[int] = Field(default=None, foreign_key="power_teams.id", index=True)

    action_type: str = Field(index=True)
    quantity: int = Field(default=1)

    source: ActionSource = Field(default=ActionSource.UNKNOWN, index=True)
    channel: ActionChannel = Field(default=ActionChannel.OTHER, index=True)

    # Dedupe retries / double-submits
    idempotency_key: Optional[str] = Field(default=None, index=True, unique=True)

    occurred_at: datetime = Field(default_factory=utcnow, index=True)

    # Structured metadata (dict). Stored as JSON in SQLite/Postgres.
    meta: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=utcnow, index=True)
