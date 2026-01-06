from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint, event
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Keep timestamps UTC-naive for consistent storage/ordering in SQLite.
    return datetime.utcnow().replace(tzinfo=None)


def _clean_slug(raw: Optional[str], max_len: int = 80) -> str:
    s = (raw or "").strip().lower()
    if not s:
        raise ValueError("slug is required")

    # Safe-ish slug normalization: keep letters/numbers/dash/underscore.
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        elif ch.isspace():
            out.append("-")

    slug = "".join(out).strip("-")
    if not slug:
        raise ValueError("slug is required")
    if len(slug) > max_len:
        slug = slug[:max_len]
    return slug


def _clean_title(raw: Optional[str], max_len: int = 140) -> str:
    s = (raw or "").strip()
    if not s:
        raise ValueError("title is required")
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _clean_description(raw: Optional[str], max_len: int = 1000) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _require_nonnegative_int(value: object, field: str) -> int:
    try:
        i = int(value)  # type: ignore[arg-type]
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if i < 0:
        raise ValueError(f"{field} must be >= 0")
    return i


def _require_positive_int(value: object, field: str) -> int:
    try:
        i = int(value)  # type: ignore[arg-type]
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if i < 1:
        raise ValueError(f"{field} must be a positive integer")
    return i


class TrainingModule(SQLModel, table=True):
    """
    A training item volunteers can complete.
    Served by /training/modules and referenced by /training/complete.
    """

    __table_args__ = (UniqueConstraint("slug", name="uq_trainingmodule_slug"),)

    id: Optional[int] = Field(default=None, primary_key=True)

    # Human-friendly stable identifier for Discord UX and future imports
    slug: str = Field(index=True, max_length=80)

    title: str = Field(max_length=140)
    description: Optional[str] = Field(default=None, max_length=1000)

    # Ordering for lists
    sort_order: int = Field(default=100, index=True)

    # Optional time estimate for volunteers
    estimated_minutes: Optional[int] = Field(default=None)

    is_active: bool = Field(default=True, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    def validate(self) -> None:
        self.slug = _clean_slug(self.slug)
        self.title = _clean_title(self.title)
        self.description = _clean_description(self.description)

        self.sort_order = _require_nonnegative_int(self.sort_order, "sort_order")

        if self.estimated_minutes is not None:
            self.estimated_minutes = _require_positive_int(self.estimated_minutes, "estimated_minutes")


@event.listens_for(TrainingModule, "before_insert")
def _trainingmodule_before_insert(mapper, connection, target: TrainingModule) -> None:  # noqa: ANN001
    # Ensure consistent timestamps + normalization when created through SQLAlchemy.
    target.created_at = target.created_at or utcnow()
    target.updated_at = utcnow()
    try:
        target.validate()
    except Exception:
        # Let the calling layer raise/handle; don’t swallow.
        raise


@event.listens_for(TrainingModule, "before_update")
def _trainingmodule_before_update(mapper, connection, target: TrainingModule) -> None:  # noqa: ANN001
    target.updated_at = utcnow()
    try:
        target.validate()
    except Exception:
        raise
