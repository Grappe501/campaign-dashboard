from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from sqlmodel import SQLModel, Field


class ImpactReachSnapshot(SQLModel, table=True):
    """
    Precomputed rollups (daily/weekly) for faster dashboards.
    """
    __tablename__ = "impact_reach_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)

    period_start: date = Field(index=True)
    period_end: date = Field(index=True)

    county_id: Optional[int] = Field(default=None, foreign_key="counties.id", index=True)
    power_team_id: Optional[int] = Field(default=None, foreign_key="power_teams.id", index=True)
    actor_person_id: Optional[int] = Field(default=None, foreign_key="people.id", index=True)

    computed_reach: float = Field(default=0.0)
    actions_total: int = Field(default=0)

    computed_at: datetime = Field(default_factory=datetime.utcnow)
