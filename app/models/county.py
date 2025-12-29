from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class County(SQLModel, table=True):
    __tablename__ = "counties"

    id: Optional[int] = Field(default=None, primary_key=True)

    # FIPS identifiers
    state_fips: str = Field(index=True, max_length=2)     # "05"
    county_fips: str = Field(index=True, max_length=3)    # "119"
    fips5: str = Field(index=True, max_length=5)          # "05119"

    # Human-readable
    name: str = Field(index=True, max_length=64)          # "Pulaski"
    slug: Optional[str] = Field(default=None, index=True, max_length=80)
    aliases: Optional[str] = Field(default=None, max_length=256)
    seat: Optional[str] = Field(default=None, max_length=64)

    # Campaign metadata
    active: bool = Field(default=True, index=True)

    region: Optional[str] = Field(default=None, index=True, max_length=64)
    metro_area: Optional[str] = Field(default=None, index=True, max_length=64)
    population_rank: Optional[int] = Field(default=None, index=True)
    priority_tier: Optional[int] = Field(default=None, index=True)

    strategy_notes: Optional[str] = Field(default=None, max_length=512)
    centroid_lat: Optional[float] = Field(default=None)
    centroid_lon: Optional[float] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    # NOTE: Relationships intentionally omitted for compatibility/stability with current SQLAlchemy/SQLModel.
    # We query via foreign keys explicitly (select/join) and can re-add relationships later with migrations.
