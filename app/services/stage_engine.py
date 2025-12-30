from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlmodel import Session

from app.models.person import Person, VolunteerStage, utcnow


@dataclass(frozen=True)
class PersonImpactStats:
    """
    Minimal stats used to determine whether a person auto-promotes through
    the activation arc based on logged impact.
    """
    actions_total: int


@dataclass(frozen=True)
class StageDecision:
    """
    Result of evaluating a potential stage change.
    """
    should_change: bool
    new_stage: Optional[VolunteerStage] = None
    reason: Optional[str] = None
    lock_stage: bool = False


# -------------------------
# Guard rails / policy
# -------------------------

GATED_STAGES = {
    VolunteerStage.TEAM,
    VolunteerStage.FUNDRAISING,
    VolunteerStage.LEADER,
}


def _is_gated(stage: VolunteerStage) -> bool:
    return stage in GATED_STAGES


def _safe_int(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def can_transition(
    person: Person,
    new_stage: VolunteerStage,
    *,
    allow_if_locked: bool = False,
) -> Tuple[bool, str]:
    """
    Central role guard for stage changes (Milestone 3).

    Rules:
    - If stage_locked=True, only allow transition when allow_if_locked=True.
      (e.g., human-approved transitions)
    - Never auto-promote into gated stages (TEAM/FUNDRAISING/LEADER).
    - You may always *keep* someone in their current stage (no-op).
    """
    if person.stage == new_stage:
        return True, "noop"

    if getattr(person, "stage_locked", False) and not allow_if_locked:
        return False, "stage_locked"

    return True, "ok"


def evaluate_auto_promotion(person: Person, stats: PersonImpactStats) -> Optional[VolunteerStage]:
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
    total = _safe_int(getattr(stats, "actions_total", 0))

    if current in (VolunteerStage.OBSERVER, VolunteerStage.NEW) and total >= 1:
        return VolunteerStage.ACTIVE

    if current == VolunteerStage.ACTIVE and total >= 5:
        return VolunteerStage.OWNER

    return None


def evaluate_stage_change_from_impact(
    person: Person,
    stats: PersonImpactStats,
) -> StageDecision:
    """
    Evaluate an auto stage change from impact logging.

    This returns a structured StageDecision so API handlers can
    report exactly what happened (stage_changed_to, reason, etc.).
    """
    next_stage = evaluate_auto_promotion(person, stats)
    if not next_stage:
        return StageDecision(should_change=False)

    # Guard: auto promotion must not enter gated stages
    if _is_gated(next_stage):
        return StageDecision(
            should_change=False,
            reason=f"auto_blocked:gated:{next_stage}",
        )

    allowed, why = can_transition(person, next_stage, allow_if_locked=False)
    if not allowed:
        return StageDecision(
            should_change=False,
            reason=f"auto_blocked:{why}",
        )

    return StageDecision(
        should_change=True,
        new_stage=next_stage,
        reason=f"auto_promo:{person.stage}->{next_stage}",
        lock_stage=False,
    )


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

    Guard behavior:
    - If person.stage_locked=True, this function assumes the caller is a human/admin path.
      (Approvals passes lock_stage=True and is treated as allow_if_locked=True.)
    """
    if not reason or not str(reason).strip():
        reason = "unspecified"

    # If stage_locked is already True, only allow stage changes on "human" paths.
    allow_if_locked = lock_stage or reason.startswith("approved:") or reason.startswith("admin:")
    allowed, why = can_transition(person, new_stage, allow_if_locked=allow_if_locked)
    if not allowed:
        # Intentionally no-op rather than raising: bot/API should not crash on a policy block.
        # The calling endpoint should decide whether to surface this in response payload.
        return

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
