from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

class Person(SQLModel, table=True):
    __tablename__ = "people"

    id: Optional[int] = Field(default=None, primary_key=True)
    # campaign-issued tracking number (stable, human-friendly)
    tracking_number: str = Field(index=True, unique=True)

    name: str
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = Field(default=None, index=True)
    discord_user_id: Optional[str] = Field(default=None, index=True)

    # lifecycle stage
    stage: str = Field(default="observer", index=True)

    # geographic placement
    region: Optional[str] = Field(default=None, index=True)
    county: Optional[str] = Field(default=None, index=True)
    city: Optional[str] = Field(default=None, index=True)
    precinct: Optional[str] = Field(default=None, index=True)

    # relational lineage (who recruited this person into the system)
    recruited_by_person_id: Optional[int] = Field(default=None, foreign_key="people.id", index=True)

    # consent + visibility flags
    allow_tracking: bool = Field(default=True)
    allow_discord_comms: bool = Field(default=True)
    allow_leaderboard: bool = Field(default=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)
