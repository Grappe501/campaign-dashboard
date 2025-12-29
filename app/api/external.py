from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Query

from ..services.census import (
    county_population,                 # legacy
    fetch_county_snapshot_acs5,
    fetch_many_county_snapshots_acs5,
)
from ..services.bls import series

router = APIRouter(prefix="/external", tags=["external"])


# ------------------------------------------------------------------
# Census (legacy / compatibility)
# ------------------------------------------------------------------

@router.get("/census/county_population")
async def get_county_population(
    state_fips: str,
    county_fips: str,
    year: str = "2023",
):
    """
    Legacy endpoint. Prefer /external/census/county_snapshot for dashboard use.
    """
    try:
        return await county_population(
            state_fips=state_fips,
            county_fips=county_fips,
            year=year,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------
# Census (dashboard-grade)
# ------------------------------------------------------------------

@router.get("/census/county_snapshot")
async def get_county_snapshot(
    state_fips: str,
    county_fips: str,
    year: str = "2023",
):
    """
    Returns a full ACS 5-year snapshot suitable for CountySnapshot ingestion.
    """
    try:
        return await fetch_county_snapshot_acs5(
            state_fips=state_fips,
            county_fips=county_fips,
            year=year,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/census/refresh_counties")
async def refresh_counties(
    state_fips: str,
    county_fips: List[str] = Query(..., description="List of 3-digit county FIPS"),
    year: str = "2023",
):
    """
    Batch Census refresh.
    Intended for:
      - your 9-county sample set
      - cron / manual refresh jobs
      - future DB upsert endpoint

    Returns a list of snapshot payloads.
    """
    try:
        return await fetch_many_county_snapshots_acs5(
            state_fips=state_fips,
            county_fips_list=county_fips,
            year=year,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------
# BLS
# ------------------------------------------------------------------

@router.get("/bls/series")
async def get_bls_series(
    series_id: str,
    start_year: str = "2022",
    end_year: str = "2025",
):
    """
    Raw BLS series fetch.
    Later we will:
      - map county -> series_id in DB
      - write unemployment into CountySnapshot
    """
    try:
        return await series(
            series_id=series_id,
            start_year=start_year,
            end_year=end_year,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
