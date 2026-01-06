from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint, event
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    # Keep timestamps timezone-naive for consistency with the rest of the project.
    return datetime.utcnow().replace(tzinfo=None)


def _require_positive_int(value: object, field: str) -> int:
    try:
        i = int(value)  # type: ignore[arg-type]
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if i < 1:
        raise ValueError(f"{field} must be a positive integer")
    return i


# Keep this small + explicit; easy to evolve later.
POWER5_STATUSES = [
    "invited",
    "onboarded",
    "active",
    "churned",
]


def normalize_status(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "invited"
    if s not in POWER5_STATUSES:
        raise ValueError(f"Invalid Power5Link.status: {s}. Allowed: {', '.join(POWER5_STATUSES)}")
    return s


class Power5Link(SQLModel, table=True):
    """
    Represents a recruitment edge inside a PowerTeam:
      parent (recruiter) -> child (recruit)

    DB guarantees we rely on:
      - A child can only appear once per team (one parent per child within a team).
    """

    __tablename__ = "power5_links"
    __table_args__ = (
        # Enforce "one child per team" at the DB level (matches API upsert behavior).
        UniqueConstraint("power_team_id", "child_person_id", name="uq_power5_links_team_child"),
        # Optional extra safety: prevents accidental duplicate edges even if logic changes.
        UniqueConstraint(
            "power_team_id",
            "parent_person_id",
            "child_person_id",
            name="uq_power5_links_team_parent_child",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    power_team_id: int = Field(foreign_key="power_teams.id", index=True)
    parent_person_id: int = Field(foreign_key="people.id", index=True)
    child_person_id: int = Field(foreign_key="people.id", index=True)

    # 1 = direct recruit of leader, 2 = recruit-of-recruit, etc.
    depth: int = Field(default=1, index=True)

    # invited | onboarded | active | churned
    status: str = Field(default="invited", index=True)

    invited_at: Optional[datetime] = Field(default_factory=_utcnow_naive, index=True)
    onboarded_at: Optional[datetime] = Field(default=None, index=True)
    activated_at: Optional[datetime] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=_utcnow_naive, index=True)

    # Convenience helpers (safe)
    def is_active(self) -> bool:
        return (self.status or "").strip().lower() == "active"

    def is_onboarded(self) -> bool:
        return (self.status or "").strip().lower() in ("onboarded", "active")

    def is_churned(self) -> bool:
        return (self.status or "").strip().lower() == "churned"


def _validate_and_normalize(target: Power5Link) -> None:
    # Validate FK-ish ints (fail closed)
    target.power_team_id = _require_positive_int(getattr(target, "power_team_id", None), "power_team_id")
    target.parent_person_id = _require_positive_int(getattr(target, "parent_person_id", None), "parent_person_id")
    target.child_person_id = _require_positive_int(getattr(target, "child_person_id", None), "child_person_id")

    # Prevent nonsense self-links
    if target.parent_person_id == target.child_person_id:
        raise ValueError("Power5Link.parent_person_id cannot equal child_person_id.")

    # Validate depth
    d = int(getattr(target, "depth", 1) or 1)
    if d < 1:
        raise ValueError("Power5Link.depth must be >= 1.")
    target.depth = d

    # Normalize status
    target.status = normalize_status(getattr(target, "status", None))

    now = _utcnow_naive()

    # Ensure created_at exists
    if not getattr(target, "created_at", None):
        target.created_at = now

    # Ensure invited_at exists (baseline timestamp)
    if not getattr(target, "invited_at", None):
        target.invited_at = now

    # Status-consistency guardrails (non-destructive; fail closed on nonsense)
    st = (getattr(target, "status", "") or "").strip().lower()

    # If onboarded/active, onboarded_at should exist
    if st in ("onboarded", "active") and getattr(target, "onboarded_at", None) is None:
        target.onboarded_at = now

    # If active, activated_at should exist
    if st == "active" and getattr(target, "activated_at", None) is None:
        target.activated_at = now

    # Sanity ordering (only compare datetimes)
    inv = getattr(target, "invited_at", None)
    ob = getattr(target, "onboarded_at", None)
    act = getattr(target, "activated_at", None)
    created = getattr(target, "created_at", None)

    if isinstance(created, datetime) and isinstance(inv, datetime) and inv < created:
        raise ValueError("Power5Link.invited_at must be >= created_at.")
    if isinstance(inv, datetime) and isinstance(ob, datetime) and ob < inv:
        raise ValueError("Power5Link.onboarded_at must be >= invited_at.")
    if isinstance(ob, datetime) and isinstance(act, datetime) and act < ob:
        raise ValueError("Power5Link.activated_at must be >= onboarded_at.")


@event.listens_for(Power5Link, "before_insert")
def _power5link_before_insert(mapper, connection, target) -> None:  # noqa: ANN001
    _validate_and_normalize(target)


@event.listens_for(Power5Link, "before_update")
def _power5link_before_update(mapper, connection, target) -> None:  # noqa: ANN001
    _validate_and_normalize(target)
