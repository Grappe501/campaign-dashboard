from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalType(str, Enum):
    """
    What kind of elevated access is being requested.
    Values are API-stable strings and are safe to store and display.
    """

    TEAM = "team_access"
    FUNDRAISING = "fundraising_access"
    LEADER = "leader_access"


class ApprovalStatus(str, Enum):
    """
    Review lifecycle state.
    """

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalRequest(SQLModel, table=True):
    """
    Human approval gate for elevated volunteer lifecycle stages.

    Notes:
    - person_id references people.id (Person table uses __tablename__="people")
    - reviewed_by_person_id also references people.id
    - API layer enforces dedupe for PENDING requests per (person_id, request_type)
    """

    __tablename__ = "approval_requests"

    id: Optional[int] = Field(default=None, primary_key=True)

    person_id: int = Field(foreign_key="people.id", index=True)
    request_type: ApprovalType = Field(index=True)

    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, index=True)
    notes: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    reviewed_at: Optional[datetime] = Field(default=None, index=True)
    reviewed_by_person_id: Optional[int] = Field(
        default=None,
        foreign_key="people.id",
        index=True,
    )
