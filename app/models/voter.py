from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

VOTER_STEPS = [
    "identified",
    "registration_checked",
    "registered",
    "vote_plan_created",
    "education_provided",
    "access_confirmed",
    "followup_scheduled",
    "voted",
]

class VoterContact(SQLModel, table=True):
    __tablename__ = "voter_contacts"

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_person_id: int = Field(foreign_key="people.id", index=True)

    # minimal identifying info (keep privacy-respecting)
    name: Optional[str] = Field(default=None)
    county: Optional[str] = Field(default=None, index=True)

    step: str = Field(default="identified", index=True)
    notes: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
