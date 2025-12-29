from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select

from app.database import init_db, session_scope
from app.models.county import County


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify_county_name(name: str) -> str:
    """
    Simple, stable slug for lookups/URLs/Discord.
    (We keep it intentionally conservative; can be upgraded later.)
    """
    return name.strip().lower().replace(" ", "-")


AR_SAMPLE_COUNTIES: List[Dict[str, str]] = [
    {"state_fips": "05", "county_fips": "119", "fips5": "05119", "name": "Pulaski"},
    {"state_fips": "05", "county_fips": "143", "fips5": "05143", "name": "Washington"},
    {"state_fips": "05", "county_fips": "031", "fips5": "05031", "name": "Craighead"},
    {"state_fips": "05", "county_fips": "007", "fips5": "05007", "name": "Benton"},
    {"state_fips": "05", "county_fips": "069", "fips5": "05069", "name": "Jefferson"},
    {"state_fips": "05", "county_fips": "131", "fips5": "05131", "name": "Sebastian"},
    {"state_fips": "05", "county_fips": "035", "fips5": "05035", "name": "Crittenden"},
    {"state_fips": "05", "county_fips": "045", "fips5": "05045", "name": "Faulkner"},
    {"state_fips": "05", "county_fips": "051", "fips5": "05051", "name": "Garland"},
]


def normalize_row(row: Dict[str, str]) -> Dict[str, str]:
    """
    Normalize/validate row values so we don't accidentally create malformed FIPS.
    """
    state_fips = str(row["state_fips"]).zfill(2)
    county_fips = str(row["county_fips"]).zfill(3)
    fips5 = str(row.get("fips5") or (state_fips + county_fips)).zfill(5)
    name = str(row["name"]).strip()

    return {
        "state_fips": state_fips,
        "county_fips": county_fips,
        "fips5": fips5,
        "name": name,
        "slug": slugify_county_name(name),
    }


def upsert_county(session, row: Dict[str, str]) -> County:
    """
    Upsert by (state_fips, county_fips) which will become a composite unique key later.

    Future-proofing:
    - also maintains `fips5` and `slug`
    - will not erase optional metadata fields (region, metro_area, etc.) if already set
    """
    row_n = normalize_row(row)

    stmt = select(County).where(
        (County.state_fips == row_n["state_fips"]) & (County.county_fips == row_n["county_fips"])
    )
    existing: Optional[County] = session.exec(stmt).first()

    if existing:
        existing.fips5 = row_n["fips5"]
        existing.name = row_n["name"]
        # only set slug if missing; keep any custom slug someone may have configured
        if getattr(existing, "slug", None) in (None, ""):
            existing.slug = row_n["slug"]
        existing.active = True
        existing.updated_at = utcnow()
        session.add(existing)
        return existing

    # Create new record. Don't set optional future fields here; keep seed minimal.
    county = County(
        state_fips=row_n["state_fips"],
        county_fips=row_n["county_fips"],
        fips5=row_n["fips5"],
        name=row_n["name"],
        slug=row_n["slug"],  # will exist if you've added it to the County model
        active=True,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(county)
    return county


def main() -> None:
    # Ensure tables exist (local dev)
    init_db()

    with session_scope() as session:
        for row in AR_SAMPLE_COUNTIES:
            upsert_county(session, row)

        total = session.exec(select(County)).all()
        active = session.exec(select(County).where(County.active == True)).all()

    print(f"Seeded/updated counties: {len(total)} (active: {len(active)})")


if __name__ == "__main__":
    main()
