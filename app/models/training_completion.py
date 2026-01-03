from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Keep timestamps UTC-naive for consistent storage/ordering in SQLite.
    return datetime.utcnow().replace(tzinfo=None)


class TrainingCompletion(SQLModel, table=True):
    """
    A record that a Person completed a TrainingModule.

    NOTE:
    - Person table in this repo is "people" (see database.py sqlite auto-migrate).
    - TrainingModule default table name becomes "trainingmodule" under SQLModel.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # Person table name is "people"
    person_id: int = Field(index=True, foreign_key="people.id")

    # TrainingModule table name defaults to "trainingmodule"
    module_id: int = Field(index=True, foreign_key="trainingmodule.id")

    completed_at: datetime = Field(default_factory=utcnow, index=True)

    note: Optional[str] = Field(default=None, max_length=500)

    created_at: datetime = Field(default_factory=utcnow, index=True)

    def validate(self) -> None:
        if self.note is not None:
            n = self.note.strip()
            self.note = n[:500] if len(n) > 500 else n
