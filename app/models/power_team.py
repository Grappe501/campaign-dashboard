from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PowerTeam(SQLModel, table=True):
    __tablename__ = "power_teams"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Who owns/leads this Power of 5 team
    leader_person_id: int = Field(foreign_key="people.id", index=True)

    # Display name for the team (default matches original behavior)
    name: str = Field(default="Power of 5")

    # Minimum goal size (default 5 = Power of 5)
    min_goal_size: int = Field(default=5)

    created_at: datetime = Field(default_factory=utcnow, index=True)


class PowerTeamMember(SQLModel, table=True):
    __tablename__ = "power_team_members"

    id: Optional[int] = Field(default=None, primary_key=True)

    power_team_id: int = Field(foreign_key="power_teams.id", index=True)

    # Canonical member FK name (aligns with Teams API + common convention)
    person_id: int = Field(foreign_key="people.id", index=True)

    joined_at: datetime = Field(default_factory=utcnow, index=True)
