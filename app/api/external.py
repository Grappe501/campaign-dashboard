from __future__ import annotations

from fastapi import APIRouter, HTTPException
from ..services.census import county_population
from ..services.bls import series

router = APIRouter(prefix="/external", tags=["external"])

@router.get("/census/county_population")
async def get_county_population(state_fips: str, county_fips: str, year: str = "2023"):
    try:
        return await county_population(state_fips=state_fips, county_fips=county_fips, year=year)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/bls/series")
async def get_bls_series(series_id: str, start_year: str = "2022", end_year: str = "2025"):
    try:
        return await series(series_id=series_id, start_year=start_year, end_year=end_year)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
