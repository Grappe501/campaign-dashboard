from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    # timezone-aware UTC for future-proofing
    return datetime.now(timezone.utc)


class VolunteerStage(str, Enum):
    """
    Volunteer lifecycle stages.

    Auto stages (safe):
    - OBSERVER: default / newsletter / curious
    - NEW: joined the hub
    - ACTIVE: logged at least 1 action
    - OWNER: consistent contributor (auto)

    Approval-gated stages (human only):
    - TEAM: trusted campaign team access
    - FUNDRAISING: anything money-related
    - LEADER: manages others / owns a lane
    - ADMIN: staff-level
    """

    OBSERVER = "observer"
    NEW = "new"
    ACTIVE = "active"
    OWNER = "owner"

    TEAM = "team"
    FUNDRAISING = "fundraising"
    LEADER = "leader"
    ADMIN = "admin"


class Person(SQLModel, table=True):
    """
    A single human in the campaign system.

    Notes:
    - tracking_number is the stable campaign-issued identifier.
    - discord_user_id is stored as a string because Discord snowflake IDs can exceed 32-bit ints.
    - stage_locked prevents auto-promotion from impact logging (approval gates must be explicit).
    """

    __tablename__ = "people"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Campaign-issued tracking number (stable, human-friendly)
    tracking_number: str = Field(index=True, unique=True)

    name: str

    # Contact details (optional)
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = Field(default=None, index=True)

    # Discord identity (string because snowflake ids can exceed int range)
    discord_user_id: Optional[str] = Field(default=None, index=True)

    # ---- Lifecycle stage ----
    # Keep the DB column name "stage" for compatibility with existing data.
    stage: VolunteerStage = Field(default=VolunteerStage.OBSERVER, index=True)

    # If True, do NOT auto-promote this person anymore (human-approved only)
    stage_locked: bool = Field(default=False, index=True)

    # Audit trail for stage changes
    stage_last_changed_at: datetime = Field(default_factory=utcnow, index=True)
    stage_changed_reason: Optional[str] = Field(
        default=None
    )  # e.g. "auto:new->active", "approved:fundraising_access"

    # ---- Geographic placement ----
    region: Optional[str] = Field(default=None, index=True)
    county: Optional[str] = Field(default=None, index=True)
    city: Optional[str] = Field(default=None, index=True)
    precinct: Optional[str] = Field(default=None, index=True)

    # ---- Relational lineage (who recruited this person) ----
    recruited_by_person_id: Optional[int] = Field(
        default=None,
        foreign_key="people.id",
        index=True,
    )

    # ---- Consent + visibility flags ----
    allow_tracking: bool = Field(default=True)
    allow_discord_comms: bool = Field(default=True)
    allow_leaderboard: bool = Field(default=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)

    # Convenience helpers (safe to use in services)
    def is_approval_gated(self) -> bool:
        return self.stage in {
            VolunteerStage.TEAM,
            VolunteerStage.FUNDRAISING,
            VolunteerStage.LEADER,
            VolunteerStage.ADMIN,
        }

    def is_stage_locked(self) -> bool:
        return bool(self.stage_locked)
