from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session

from app.models.person import Person, VolunteerStage, utcnow


@dataclass(frozen=True)
class PersonImpactStats:
    """
    Minimal stats used to determine whether a person auto-promotes through
    the activation arc based on logged impact.
    """
    actions_total: int


def evaluate_auto_promotion(
    person: Person,
    stats: PersonImpactStats,
) -> Optional[VolunteerStage]:
    """
    Returns the next stage if an auto-promotion should occur, else None.

    Rules:
    - If stage is locked, never auto-promote.
    - Never auto-promote into approval-gated stages (TEAM/FUNDRAISING/LEADER).
    - Observer/New -> Active after 1+ actions
    - Active -> Owner after 5+ actions
    """
    if getattr(person, "stage_locked", False):
        return None

    current = person.stage
    total = int(getattr(stats, "actions_total", 0) or 0)

    if current in (VolunteerStage.OBSERVER, VolunteerStage.NEW) and total >= 1:
        return VolunteerStage.ACTIVE

    if current == VolunteerStage.ACTIVE and total >= 5:
        return VolunteerStage.OWNER

    return None


def apply_stage_change(
    session: Session,
    person: Person,
    new_stage: VolunteerStage,
    reason: str,
    lock_stage: bool = False,
) -> None:
    """
    Apply a stage update + audit fields.

    Safe behavior:
    - No-op if stage unchanged AND lock_stage=False.
    - If lock_stage=True, will set stage_locked even if stage is unchanged.
    - Commits and refreshes the person.
    """
    if not reason or not str(reason).strip():
        reason = "unspecified"

    stage_changed = person.stage != new_stage
    lock_changed = lock_stage and not getattr(person, "stage_locked", False)

    if not stage_changed and not lock_changed:
        return

    if stage_changed:
        person.stage = new_stage
        person.stage_last_changed_at = utcnow()
        person.stage_changed_reason = reason

    if lock_stage:
        person.stage_locked = True

    session.add(person)
    session.commit()
    session.refresh(person)
