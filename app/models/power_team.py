from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

class PowerTeam(SQLModel, table=True):
    __tablename__ = "power_teams"

    id: Optional[int] = Field(default=None, primary_key=True)
    leader_person_id: int = Field(foreign_key="people.id", index=True)

    name: str = Field(default="Power of 5")
    min_goal_size: int = Field(default=5)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PowerTeamMember(SQLModel, table=True):
    __tablename__ = "power_team_members"

    id: Optional[int] = Field(default=None, primary_key=True)
    power_team_id: int = Field(foreign_key="power_teams.id", index=True)
    member_person_id: int = Field(foreign_key="people.id", index=True)

    joined_at: datetime = Field(default_factory=datetime.utcnow)
