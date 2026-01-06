from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint, event
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Keep timestamps UTC-naive for consistent storage/ordering in SQLite.
    return datetime.utcnow().replace(tzinfo=None)


def _clean_name(raw: Optional[str], fallback: str = "Power of 5", max_len: int = 120) -> str:
    s = (raw or "").strip()
    if not s:
        s = fallback
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _require_positive_int(value: object, field: str) -> int:
    try:
        i = int(value)  # type: ignore[arg-type]
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if i < 1:
        raise ValueError(f"{field} must be a positive integer")
    return i


class PowerTeam(SQLModel, table=True):
    __tablename__ = "power_teams"
    __table_args__ = (
        # Critical for the “universal pathway”: one PowerTeam per leader.
        UniqueConstraint("leader_person_id", name="uq_power_teams_leader_person"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # Who owns/leads this Power of 5 team
    leader_person_id: int = Field(foreign_key="people.id", index=True)

    # Display name for the team (default matches original behavior)
    name: str = Field(default="Power of 5", index=True)

    # Minimum goal size (default 5 = Power of 5)
    min_goal_size: int = Field(default=5)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class PowerTeamMember(SQLModel, table=True):
    __tablename__ = "power_team_members"
    __table_args__ = (
        # Prevent duplicate memberships in the same team.
        UniqueConstraint("power_team_id", "person_id", name="uq_power_team_members_team_person"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    power_team_id: int = Field(foreign_key="power_teams.id", index=True)

    # Canonical member FK name (aligns with Teams API + common convention)
    person_id: int = Field(foreign_key="people.id", index=True)

    joined_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


# --- Validation + auto-touch timestamps ---


@event.listens_for(PowerTeam, "before_insert")
def _powerteam_before_insert(mapper, connection, target) -> None:  # noqa: ANN001
    # Validate core fields
    target.leader_person_id = _require_positive_int(getattr(target, "leader_person_id", None), "leader_person_id")

    try:
        mgs = int(getattr(target, "min_goal_size", 5) or 5)
    except Exception:
        raise ValueError("min_goal_size must be an integer")
    if mgs < 1:
        raise ValueError("min_goal_size must be >= 1")
    target.min_goal_size = mgs

    target.name = _clean_name(getattr(target, "name", None), fallback="Power of 5")

    now = utcnow()
    if not getattr(target, "created_at", None):
        target.created_at = now
    target.updated_at = now


@event.listens_for(PowerTeam, "before_update")
def _powerteam_before_update(mapper, connection, target) -> None:  # noqa: ANN001
    # Keep updates safe too
    target.leader_person_id = _require_positive_int(getattr(target, "leader_person_id", None), "leader_person_id")

    try:
        mgs = int(getattr(target, "min_goal_size", 5) or 5)
    except Exception:
        raise ValueError("min_goal_size must be an integer")
    if mgs < 1:
        raise ValueError("min_goal_size must be >= 1")
    target.min_goal_size = mgs

    target.name = _clean_name(getattr(target, "name", None), fallback="Power of 5")

    target.updated_at = utcnow()


@event.listens_for(PowerTeamMember, "before_insert")
def _powerteammember_before_insert(mapper, connection, target) -> None:  # noqa: ANN001
    # Validate FKs
    target.power_team_id = _require_positive_int(getattr(target, "power_team_id", None), "power_team_id")
    target.person_id = _require_positive_int(getattr(target, "person_id", None), "person_id")

    now = utcnow()
    if not getattr(target, "joined_at", None):
        target.joined_at = now
    target.updated_at = now


@event.listens_for(PowerTeamMember, "before_update")
def _powerteammember_before_update(mapper, connection, target) -> None:  # noqa: ANN001
    target.power_team_id = _require_positive_int(getattr(target, "power_team_id", None), "power_team_id")
    target.person_id = _require_positive_int(getattr(target, "person_id", None), "person_id")
    target.updated_at = utcnow()
