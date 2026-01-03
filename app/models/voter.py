from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import event
from sqlmodel import Field, SQLModel

VOTER_STEPS = (
    "identified",
    "registration_checked",
    "registered",
    "vote_plan_created",
    "education_provided",
    "access_confirmed",
    "followup_scheduled",
    "voted",
)


def _utcnow_naive() -> datetime:
    # Keep timestamps timezone-naive for consistency with the rest of the project.
    return datetime.utcnow().replace(tzinfo=None)


class VoterContact(SQLModel, table=True):
    __tablename__ = "voter_contacts"

    id: Optional[int] = Field(default=None, primary_key=True)

    owner_person_id: int = Field(foreign_key="people.id", index=True)

    # minimal identifying info (keep privacy-respecting)
    name: Optional[str] = Field(default=None)
    county: Optional[str] = Field(default=None, index=True)

    # Track the voter-support workflow step (validated below)
    step: str = Field(default="identified", index=True)

    notes: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow_naive)
    updated_at: datetime = Field(default_factory=_utcnow_naive)

    @staticmethod
    def normalize_step(raw: Optional[str]) -> str:
        s = (raw or "").strip().lower()
        if not s:
            return "identified"
        if s not in VOTER_STEPS:
            raise ValueError(f"Invalid voter step: {s}. Allowed: {', '.join(VOTER_STEPS)}")
        return s


# --- Auto-touch timestamps + validate step on write ---


@event.listens_for(VoterContact, "before_insert")
def _votercontact_before_insert(mapper, connection, target) -> None:  # noqa: ANN001
    target.step = VoterContact.normalize_step(getattr(target, "step", None))

    now = _utcnow_naive()
    if not getattr(target, "created_at", None):
        target.created_at = now
    target.updated_at = now


@event.listens_for(VoterContact, "before_update")
def _votercontact_before_update(mapper, connection, target) -> None:  # noqa: ANN001
    target.step = VoterContact.normalize_step(getattr(target, "step", None))
    target.updated_at = _utcnow_naive()


__all__ = ["VoterContact", "VOTER_STEPS"]
