from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

EVENT_TYPES = [
    "power_team_meeting",
    "popcorn_party",
    "coffee_chat",
    "backyard_bbq",
    "watch_party",
    "rally",
    "training",
    "other",
]

class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    host_person_id: int = Field(foreign_key="people.id", index=True)

    event_type: str = Field(default="popcorn_party", index=True)
    title: str = Field(default="Popcorn Party")
    description: Optional[str] = Field(default=None)

    location: Optional[str] = Field(default=None)
    is_private: bool = Field(default=True)

    start_time: datetime = Field(index=True)
    end_time: Optional[datetime] = Field(default=None, index=True)

    candidate_requested: bool = Field(default=False)
    expected_size: Optional[int] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
