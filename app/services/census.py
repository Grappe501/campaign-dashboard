from __future__ import annotations

from typing import Any, Dict, Optional
import httpx
from ..config import settings

CENSUS_BASE = "https://api.census.gov/data"

async def county_population(state_fips: str, county_fips: str, year: str = "2023") -> Dict[str, Any]:
    if not settings.census_api_key:
        raise RuntimeError("CENSUS_API_KEY is not set in .env")

    # ACS 1-year profile as example: DP05 (Population and Housing Occupancy Status)
    # Total population: DP05_0001E
    url = f"{CENSUS_BASE}/{year}/acs/acs1/profile"
    params = {
        "get": "NAME,DP05_0001E",
        "for": f"county:{county_fips}",
        "in": f"state:{state_fips}",
        "key": settings.census_api_key,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    # data is [header_row, value_row]
    header = data[0]
    values = data[1]
    obj = dict(zip(header, values))
    return {
        "name": obj.get("NAME"),
        "total_population": int(obj.get("DP05_0001E")) if obj.get("DP05_0001E") else None,
        "state_fips": state_fips,
        "county_fips": county_fips,
        "year": year,
        "source": "US Census ACS 1-year profile",
    }
