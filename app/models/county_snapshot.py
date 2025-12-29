from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CountySnapshot(SQLModel, table=True):
    __tablename__ = "county_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)

    county_id: int = Field(index=True, foreign_key="counties.id")
    as_of_date: date = Field(index=True)

    # Census core
    population_total: Optional[int] = Field(default=None)
    age_18_24: Optional[int] = Field(default=None)

    pct_bachelors_or_higher: Optional[float] = Field(default=None)
    pop_25_plus: Optional[int] = Field(default=None)
    bachelors_or_higher_count: Optional[int] = Field(default=None)

    median_household_income: Optional[int] = Field(default=None)

    poverty_pct: Optional[float] = Field(default=None)
    poverty_count: Optional[int] = Field(default=None)
    poverty_universe: Optional[int] = Field(default=None)

    college_enrollment_18_24: Optional[int] = Field(default=None)
    college_enrollment_total: Optional[int] = Field(default=None)

    # BLS optional
    unemployment_rate: Optional[float] = Field(default=None)
    bls_series_id_unemployment: Optional[str] = Field(default=None, max_length=32)
    unemployment_as_of: Optional[date] = Field(default=None)

    # Provenance
    dataset_name: Optional[str] = Field(default=None, max_length=32, index=True)
    dataset_year: Optional[int] = Field(default=None, index=True)
    source_census: Optional[str] = Field(default=None, max_length=64)
    source_bls: Optional[str] = Field(default=None, max_length=64)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    # NOTE: Relationship intentionally omitted (see County model note).
