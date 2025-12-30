from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Set

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


_APPROVAL_GATED_STAGES: Set[VolunteerStage] = {
    VolunteerStage.TEAM,
    VolunteerStage.FUNDRAISING,
    VolunteerStage.LEADER,
    VolunteerStage.ADMIN,
}


class Person(SQLModel, table=True):
    """
    A single human in the campaign system.

    Notes:
    - tracking_number is the stable campaign-issued identifier.
    - discord_user_id is stored as a string because Discord snowflake IDs can exceed 32-bit ints.
    - stage_locked prevents auto-promotion from impact logging (approval gates must be explicit).
    - *_access flags are the canonical stored permissions booleans used by API + bot payloads.
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

    # ---- Onboarding + permissions ----
    # If set, user has completed onboarding (even if they remain OBSERVER/NEW)
    onboarded_at: Optional[datetime] = Field(default=None, index=True)

    # Optional lightweight audit for Discord onboarding + sync
    last_seen_discord_guild_id: Optional[str] = Field(default=None, index=True)
    last_seen_discord_channel_id: Optional[str] = Field(default=None, index=True)
    last_seen_discord_username: Optional[str] = Field(default=None)

    # Canonical access booleans (API-stable)
    team_access: bool = Field(default=False, index=True)
    fundraising_access: bool = Field(default=False, index=True)

    # Added for parity with approvals request types (team_access/fundraising_access/leader_access)
    leader_access: bool = Field(default=False, index=True)

    # If True, the user is an admin in the dashboard (NOT necessarily a Discord admin).
    # Kept as-is for backward compatibility with existing code/data.
    is_admin: bool = Field(default=False, index=True)

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

    # -------------------------
    # Convenience helpers (safe to use in services)
    # -------------------------

    def is_approval_gated(self) -> bool:
        return self.stage in _APPROVAL_GATED_STAGES

    def is_stage_locked(self) -> bool:
        return bool(self.stage_locked)

    def mark_onboarded(self) -> None:
        if self.onboarded_at is None:
            self.onboarded_at = utcnow()

    def note_discord_seen(
        self,
        *,
        guild_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> None:
        """
        Lightweight "last seen" capture used by onboarding + Discord sync hardening.
        Safe to call frequently.
        """
        if guild_id:
            self.last_seen_discord_guild_id = guild_id
        if channel_id:
            self.last_seen_discord_channel_id = channel_id
        if username:
            self.last_seen_discord_username = username

    # Optional helpers for code readability (no DB impact)
    def has_team_access(self) -> bool:
        return bool(self.team_access)

    def has_fundraising_access(self) -> bool:
        return bool(self.fundraising_access)

    def has_leader_access(self) -> bool:
        return bool(self.leader_access)

    def has_admin_access(self) -> bool:
        return bool(self.is_admin)
