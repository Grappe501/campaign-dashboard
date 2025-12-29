from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AliceCounty(SQLModel, table=True):
    __tablename__ = "alice_county"

    id: Optional[int] = Field(default=None, primary_key=True)

    county_id: int = Field(index=True, foreign_key="counties.id")
    year: int = Field(index=True)

    households_total: Optional[int] = Field(default=None)

    households_poverty: Optional[int] = Field(default=None)
    households_alice: Optional[int] = Field(default=None)
    households_below_alice_threshold: Optional[int] = Field(default=None)

    pct_poverty: Optional[float] = Field(default=None)
    pct_alice: Optional[float] = Field(default=None)
    pct_below_alice_threshold: Optional[float] = Field(default=None)

    source: Optional[str] = Field(default=None, max_length=128)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    # NOTE: Relationship intentionally omitted (see County model note).
