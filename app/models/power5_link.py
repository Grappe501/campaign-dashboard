from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Power5Link(SQLModel, table=True):
    """
    Represents a recruitment edge inside a PowerTeam:
      parent (recruiter) -> child (recruit)
    """
    __tablename__ = "power5_links"

    id: Optional[int] = Field(default=None, primary_key=True)

    power_team_id: int = Field(foreign_key="power_teams.id", index=True)
    parent_person_id: int = Field(foreign_key="people.id", index=True)
    child_person_id: int = Field(foreign_key="people.id", index=True)

    # 1 = direct recruit of leader, 2 = recruit-of-recruit, etc.
    depth: int = Field(default=1, index=True)

    # invited | onboarded | active | churned
    status: str = Field(default="invited", index=True)

    invited_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    onboarded_at: Optional[datetime] = Field(default=None)
    activated_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
