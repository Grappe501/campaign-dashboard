from __future__ import annotations

from typing import Any, Dict, List
import httpx
from ..config import settings

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

async def series(series_id: str, start_year: str = "2022", end_year: str = "2025") -> Dict[str, Any]:
    if not settings.bls_api_key:
        raise RuntimeError("BLS_API_KEY is not set in .env")

    payload = {
        "seriesid": [series_id],
        "startyear": start_year,
        "endyear": end_year,
        "registrationkey": settings.bls_api_key,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(BLS_URL, json=payload)
        r.raise_for_status()
        data = r.json()
    status = data.get("status")
    if status != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS request failed: {data}")
    series_list = data["Results"]["series"]
    return {"series_id": series_id, "results": series_list[0]}
