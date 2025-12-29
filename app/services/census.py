from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

import httpx

from ..config import settings

CENSUS_BASE = "https://api.census.gov/data"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


async def _census_get_json(url: str, params: Dict[str, Any], client: httpx.AsyncClient) -> List[List[str]]:
    r = await client.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError(f"Unexpected Census response format from {url}")
    return data


async def fetch_county_snapshot_acs5(
    state_fips: str,
    county_fips: str,
    year: str = "2023",
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Returns a stable, county-level snapshot using ACS 5-year (recommended).

    Fields returned are designed to map directly into CountySnapshot:
      - population_total
      - age_18_24
      - pct_bachelors_or_higher (+ pop_25_plus denom)
      - median_household_income
      - poverty_pct (+ poverty_count + poverty_universe)
      - college_enrollment_18_24 (optional)
      - college_enrollment_total (optional)
    """
    if not settings.census_api_key:
        raise RuntimeError("CENSUS_API_KEY is not set in .env")

    # --- Endpoints ---
    # ACS 5-year:
    #   - population + age breakdown: acs/acs5 (B tables)
    #   - education: acs/acs5 (B15003)
    #   - income: acs/acs5 (B19013)
    #   - poverty: acs/acs5 (B17001)
    #   - enrollment: acs/acs5 (B14001) [total enrollment] and B14005? (more granular)
    #
    # We'll keep it simple and robust:
    #   age_18_24 from B01001
    #   bachelors+ from B15003
    #   income from B19013
    #   poverty from B17001
    #   enrollment totals from B14001 (total enrolled) and infer 18–24 later if you want more precision
    #
    # NOTE: For 18–24 enrolled-in-college specifically, there are ACS tables, but they’re more complex.
    # We’ll add the precise “college enrolled 18–24” in a follow-up once core snapshot is flowing.

    base_url = f"{CENSUS_BASE}/{year}/acs/acs5"

    # ---- Variables ----
    # Total population
    pop_total = "B01003_001E"

    # Age 18-24: sum male (7-10) + female (31-34) in B01001
    age_vars = [
        "B01001_007E", "B01001_008E", "B01001_009E", "B01001_010E",
        "B01001_031E", "B01001_032E", "B01001_033E", "B01001_034E",
    ]

    # Education attainment B15003:
    # Total (25+ educational attainment universe): B15003_001E
    # Bachelor+: 22-25
    edu_total = "B15003_001E"
    edu_bach_plus = ["B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E"]

    # Median household income
    med_income = "B19013_001E"

    # Poverty universe and below poverty
    pov_universe = "B17001_001E"
    pov_below = "B17001_002E"

    # Enrollment (total enrolled in school) - coarse but reliable
    # B14001_001E = total, B14001_002E = enrolled, B14001_003E = not enrolled
    enroll_total = "B14001_001E"
    enroll_enrolled = "B14001_002E"

    vars_to_get = ["NAME", pop_total, edu_total, med_income, pov_universe, pov_below, enroll_total, enroll_enrolled]
    vars_to_get += age_vars
    vars_to_get += edu_bach_plus

    params = {
        "get": ",".join(vars_to_get),
        "for": f"county:{county_fips}",
        "in": f"state:{state_fips}",
        "key": settings.census_api_key,
    }

    own_client = None
    if client is None:
        own_client = httpx.AsyncClient(timeout=30)
        client = own_client

    try:
        data = await _census_get_json(base_url, params=params, client=client)
        header = data[0]
        values = data[1]
        obj = dict(zip(header, values))

        population_total = _to_int(obj.get(pop_total))

        age_18_24 = sum(_to_int(obj.get(v)) or 0 for v in age_vars) if population_total is not None else None

        edu_universe = _to_int(obj.get(edu_total))
        bach_plus = sum(_to_int(obj.get(v)) or 0 for v in edu_bach_plus) if edu_universe is not None else None
        pct_bach_plus = (bach_plus / edu_universe) if (bach_plus is not None and edu_universe and edu_universe > 0) else None

        median_household_income = _to_int(obj.get(med_income))

        poverty_universe_val = _to_int(obj.get(pov_universe))
        poverty_count_val = _to_int(obj.get(pov_below))
        poverty_pct = (poverty_count_val / poverty_universe_val) if (poverty_count_val is not None and poverty_universe_val and poverty_universe_val > 0) else None

        # Enrollment (coarse)
        # total enrolled in school (all ages)
        enrolled_total = _to_int(obj.get(enroll_enrolled))

        return {
            "name": obj.get("NAME"),
            "state_fips": state_fips,
            "county_fips": county_fips,
            "year": int(year),
            "dataset_name": "ACS5",
            "as_of": _utcnow_iso(),
            # Snapshot fields
            "population_total": population_total,
            "age_18_24": age_18_24,
            "pop_25_plus": edu_universe,
            "pct_bachelors_or_higher": _to_float(pct_bach_plus),
            "median_household_income": median_household_income,
            "poverty_universe": poverty_universe_val,
            "poverty_count": poverty_count_val,
            "poverty_pct": _to_float(poverty_pct),
            "college_enrollment_total": enrolled_total,
            # Leave 18–24 enrollment as None until we add the precise table pull
            "college_enrollment_18_24": None,
            "source": f"US Census ACS 5-year ({year})",
        }
    finally:
        if own_client is not None:
            await own_client.aclose()


async def fetch_many_county_snapshots_acs5(
    state_fips: str,
    county_fips_list: List[str],
    year: str = "2023",
) -> List[Dict[str, Any]]:
    """
    Batch fetch for multiple counties using one AsyncClient.
    Use this for your 9-county refresh job.
    """
    if not settings.census_api_key:
        raise RuntimeError("CENSUS_API_KEY is not set in .env")

    results: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for cf in county_fips_list:
            snap = await fetch_county_snapshot_acs5(
                state_fips=state_fips,
                county_fips=cf,
                year=year,
                client=client,
            )
            results.append(snap)
    return results


# Backward-compatible helper for your earlier call sites
async def county_population(state_fips: str, county_fips: str, year: str = "2023") -> Dict[str, Any]:
    """
    Compatibility wrapper. Prefer fetch_county_snapshot_acs5 for the dashboard.
    """
    snap = await fetch_county_snapshot_acs5(state_fips=state_fips, county_fips=county_fips, year=year)
    return {
        "name": snap.get("name"),
        "total_population": snap.get("population_total"),
        "state_fips": state_fips,
        "county_fips": county_fips,
        "year": year,
        "source": snap.get("source"),
    }
