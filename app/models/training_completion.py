from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint, event
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Keep timestamps UTC-naive for consistent storage/ordering in SQLite.
    return datetime.utcnow().replace(tzinfo=None)


def _require_positive_int(value: object, field: str) -> int:
    try:
        i = int(value)  # type: ignore[arg-type]
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if i < 1:
        raise ValueError(f"{field} must be >= 1")
    return i


def _clean_note(raw: Optional[str], max_len: int = 500) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[:max_len]
    return s


class TrainingCompletion(SQLModel, table=True):
    """
    A record that a Person completed a TrainingModule.

    Table/foreign key notes:
    - Person table in this repo is "people"
    - TrainingModule default table name becomes "trainingmodule" under SQLModel
    """

    __table_args__ = (
        # Prevent duplicates: a person can only complete a module once.
        UniqueConstraint("person_id", "module_id", name="uq_trainingcompletion_person_module"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # Person table name is "people"
    person_id: int = Field(index=True, foreign_key="people.id")

    # TrainingModule table name defaults to "trainingmodule"
    module_id: int = Field(index=True, foreign_key="trainingmodule.id")

    completed_at: datetime = Field(default_factory=utcnow, index=True)

    note: Optional[str] = Field(default=None, max_length=500)

    created_at: datetime = Field(default_factory=utcnow, index=True)

    def validate(self) -> None:
        self.person_id = _require_positive_int(self.person_id, "person_id")
        self.module_id = _require_positive_int(self.module_id, "module_id")
        self.note = _clean_note(self.note)


@event.listens_for(TrainingCompletion, "before_insert")
def _trainingcompletion_before_insert(mapper, connection, target: TrainingCompletion) -> None:  # noqa: ANN001
    target.created_at = target.created_at or utcnow()
    target.completed_at = target.completed_at or utcnow()
    target.validate()


@event.listens_for(TrainingCompletion, "before_update")
def _trainingcompletion_before_update(mapper, connection, target: TrainingCompletion) -> None:  # noqa: ANN001
    # We don’t maintain updated_at on completions, but we *do* keep data normalized.
    target.validate()
