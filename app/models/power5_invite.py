from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import SQLModel, Field


class Power5Invite(SQLModel, table=True):
    """
    Magic-link onboarding stub (email/sms/discord).
    Store only a token hash; return the raw token once at creation time.
    """
    __tablename__ = "power5_invites"

    id: Optional[int] = Field(default=None, primary_key=True)

    power_team_id: int = Field(foreign_key="power_teams.id", index=True)
    invited_by_person_id: int = Field(foreign_key="people.id", index=True)

    invitee_person_id: Optional[int] = Field(default=None, foreign_key="people.id", index=True)

    # email | sms | discord
    channel: str = Field(index=True)
    destination: str = Field(index=True)

    token_hash: str = Field(index=True, unique=True)

    expires_at: datetime = Field(default_factory=lambda: datetime.utcnow() + timedelta(days=7), index=True)
    consumed_at: Optional[datetime] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)
