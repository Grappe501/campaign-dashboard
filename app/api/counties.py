from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.models.county import County
from app.models.county_snapshot import CountySnapshot
from app.models.alice_county import AliceCounty

# Census services (supports both batch + single; batch is preferred)
try:
    from app.services.census import fetch_many_county_snapshots_acs5, fetch_county_snapshot_acs5
except Exception:  # pragma: no cover
    fetch_many_county_snapshots_acs5 = None  # type: ignore
    fetch_county_snapshot_acs5 = None  # type: ignore

router = APIRouter(prefix="/counties", tags=["counties"])


def _safe_exec_first(db: Session, stmt):
    """
    Avoids hard crashes if a table doesn't exist yet during early dev.
    """
    try:
        return db.exec(stmt).first()
    except OperationalError:
        return None


def _compute_as_of_date(year: int) -> date:
    # For ACS dataset vintages, Jan 1 of the dataset year is a stable convention.
    return date(year, 1, 1)


def _snapshot_key_payload(payload: Dict[str, Any], county_id: int, as_of: date) -> Tuple[int, date, Optional[str], Optional[int]]:
    dataset_name = payload.get("dataset_name") or "ACS5"
    dataset_year = payload.get("year") if isinstance(payload.get("year"), int) else None
    return (county_id, as_of, dataset_name, dataset_year)


def _upsert_snapshot(
    db: Session,
    county: County,
    payload: Dict[str, Any],
    *,
    year: int,
) -> Tuple[str, CountySnapshot]:
    """
    Upsert snapshot by (county_id, as_of_date, dataset_name, dataset_year).

    Returns ("created"|"updated", snapshot)
    """
    as_of = _compute_as_of_date(year)
    dataset_name = payload.get("dataset_name") or "ACS5"
    dataset_year = payload.get("year")
    dataset_year = int(dataset_year) if dataset_year is not None else year

    existing = _safe_exec_first(
        db,
        select(CountySnapshot).where(
            (CountySnapshot.county_id == county.id)
            & (CountySnapshot.as_of_date == as_of)
            & (CountySnapshot.dataset_name == dataset_name)
            & (CountySnapshot.dataset_year == dataset_year)
        ),
    )

    # Map payload -> model fields (only set known fields; ignore extras safely)
    updates: Dict[str, Any] = {
        "county_id": county.id,
        "as_of_date": as_of,
        "dataset_name": dataset_name,
        "dataset_year": dataset_year,
        "source_census": payload.get("source"),

        "population_total": payload.get("population_total"),
        "age_18_24": payload.get("age_18_24"),

        "pct_bachelors_or_higher": payload.get("pct_bachelors_or_higher"),
        "pop_25_plus": payload.get("pop_25_plus"),
        "bachelors_or_higher_count": payload.get("bachelors_or_higher_count"),

        "median_household_income": payload.get("median_household_income"),

        "poverty_pct": payload.get("poverty_pct"),
        "poverty_count": payload.get("poverty_count"),
        "poverty_universe": payload.get("poverty_universe"),

        "college_enrollment_18_24": payload.get("college_enrollment_18_24"),
        "college_enrollment_total": payload.get("college_enrollment_total"),
    }

    # Remove keys that are None so we don't overwrite existing values with null
    # (useful when later we add more precise college tables, etc.)
    updates = {k: v for k, v in updates.items() if v is not None}

    if existing:
        for k, v in updates.items():
            setattr(existing, k, v)
        db.add(existing)
        return ("updated", existing)

    snapshot = CountySnapshot(**updates)
    db.add(snapshot)
    return ("created", snapshot)


@router.get("/")
def list_counties(db: Session = Depends(get_db)):
    counties = db.exec(select(County).where(County.active == True).order_by(County.name)).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "state_fips": c.state_fips,
            "county_fips": c.county_fips,
            "fips5": c.fips5,
            "active": c.active,
        }
        for c in counties
    ]


@router.get("/by-name/{name}")
def get_county_by_name(name: str, db: Session = Depends(get_db)):
    county = db.exec(select(County).where(County.name.ilike(name))).first()
    if not county:
        raise HTTPException(status_code=404, detail="County not found")
    return {"fips5": county.fips5, "name": county.name}


@router.get("/{fips5}")
def get_county(fips5: str, db: Session = Depends(get_db)):
    county = db.exec(select(County).where(County.fips5 == fips5)).first()
    if not county:
        raise HTTPException(status_code=404, detail="County not found")

    latest_snapshot = _safe_exec_first(
        db,
        select(CountySnapshot)
        .where(CountySnapshot.county_id == county.id)
        .order_by(CountySnapshot.as_of_date.desc())
        .limit(1),
    )

    latest_alice = _safe_exec_first(
        db,
        select(AliceCounty)
        .where(AliceCounty.county_id == county.id)
        .order_by(AliceCounty.year.desc())
        .limit(1),
    )

    return {
        "county": {
            "id": county.id,
            "name": county.name,
            "state_fips": county.state_fips,
            "county_fips": county.county_fips,
            "fips5": county.fips5,
            "active": county.active,
        },
        "latest_snapshot": latest_snapshot.model_dump() if latest_snapshot else None,
        "latest_alice": latest_alice.model_dump() if latest_alice else None,
    }


@router.post("/refresh-snapshots")
async def refresh_snapshots(
    db: Session = Depends(get_db),
    year: int = Query(default=2023, ge=2005, le=2100),
    dry_run: bool = Query(default=False, description="If true, fetch and compute but do not write to DB"),
):
    """
    Fetch ACS5 snapshot data for all active counties and upsert into county_snapshots.

    - Uses Census ACS 5-year for stability at the county level.
    - Writes rows keyed by (county_id, as_of_date, dataset_name, dataset_year).
    """
    counties = db.exec(select(County).where(County.active == True).order_by(County.name)).all()
    if not counties:
        return {"updated": 0, "created": 0, "failed": 0, "details": []}

    # Build list of county fips per state (right now you’re using AR only; this supports multi-state later)
    # We'll group by state_fips so we can batch call per state if you expand.
    by_state: Dict[str, List[County]] = {}
    for c in counties:
        by_state.setdefault(c.state_fips, []).append(c)

    created = 0
    updated = 0
    failed = 0
    details: List[Dict[str, Any]] = []

    for state_fips, cs in by_state.items():
        county_fips_list = [c.county_fips for c in cs]

        # Preferred: batch fetch using one HTTP client
        payloads: List[Dict[str, Any]] = []
        if fetch_many_county_snapshots_acs5 is not None:
            payloads = await fetch_many_county_snapshots_acs5(state_fips=state_fips, county_fips_list=county_fips_list, year=str(year))
        elif fetch_county_snapshot_acs5 is not None:
            # Fallback: fetch one-by-one
            for c in cs:
                payloads.append(await fetch_county_snapshot_acs5(state_fips=state_fips, county_fips=c.county_fips, year=str(year)))
        else:
            raise HTTPException(status_code=500, detail="Census snapshot service is not available")

        # Index payloads by county_fips
        payload_by_cf: Dict[str, Dict[str, Any]] = {p.get("county_fips"): p for p in payloads if p.get("county_fips")}

        for c in cs:
            try:
                payload = payload_by_cf.get(c.county_fips)
                if not payload:
                    failed += 1
                    details.append({"fips5": c.fips5, "name": c.name, "status": "failed", "error": "missing payload"})
                    continue

                if dry_run:
                    details.append({"fips5": c.fips5, "name": c.name, "status": "dry_run", "dataset": payload.get("dataset_name"), "year": payload.get("year")})
                    continue

                status, snap = _upsert_snapshot(db, c, payload, year=year)
                if status == "created":
                    created += 1
                else:
                    updated += 1

                details.append({"fips5": c.fips5, "name": c.name, "status": status, "as_of_date": str(snap.as_of_date), "dataset": snap.dataset_name, "year": snap.dataset_year})
            except Exception as e:
                failed += 1
                details.append({"fips5": c.fips5, "name": c.name, "status": "failed", "error": str(e)})

    if not dry_run:
        db.commit()

    return {
        "created": created,
        "updated": updated,
        "failed": failed,
        "counties": len(counties),
        "year": year,
        "dry_run": dry_run,
        "details": details,
    }
