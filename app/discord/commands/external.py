from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from discord import app_commands

from .shared import api_request, format_api_error

if TYPE_CHECKING:
    import discord
    import httpx


def _safe_str(x: Any, max_len: int = 200) -> str:
    s = "" if x is None else str(x)
    s = s.strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _normalize_state_fips(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (state_fips_2, error_msg_or_none)
    """
    s = _digits_only(_safe_str(raw, 20))
    if not s:
        return None, "❌ state_fips is required (AR = 05)."
    if len(s) > 2:
        return None, "❌ state_fips must be 1–2 digits (AR = 05)."
    return s.zfill(2), None


def _normalize_county_fips(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (county_fips_3, error_msg_or_none)
    """
    s = _digits_only(_safe_str(raw, 20))
    if not s:
        return None, "❌ county_fips is required (3 digits, e.g., Pulaski = 119)."
    if len(s) > 3:
        return None, "❌ county_fips must be 1–3 digits (Pulaski = 119)."
    return s.zfill(3), None


def _normalize_year(raw: str, *, default: int, min_year: int, max_year: int) -> int:
    s = _digits_only(_safe_str(raw, 32))
    try:
        y = int(s) if s else int(default)
    except Exception:
        y = int(default)
    return max(min_year, min(y, max_year))


def _normalize_series_id(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Basic safety validation for BLS series id.
    - trimmed
    - capped length
    - must be non-empty
    """
    sid = _safe_str(raw, 64)
    if not sid:
        return None, "❌ series_id is required."
    # Keep permissive; BLS IDs are often alphanumeric with punctuation. Just avoid absurd lengths.
    if len(sid) < 5:
        return None, "❌ series_id looks too short. Please paste a full BLS series id."
    return sid, None


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    External lookup commands (proxy through dashboard backend).

    Provides:
      - /census (GET /external/census/county_population)
      - /bls    (GET /external/bls/series)
    """

    @tree.command(
        name="census",
        description="Census lookup: county population (ACS). Requires CENSUS_API_KEY in backend env.",
    )
    @app_commands.describe(
        state_fips="State FIPS (AR = 05)",
        county_fips="County FIPS (3 digits, e.g., Pulaski = 119)",
        year="ACS year (default 2023)",
    )
    async def census(
        interaction: "discord.Interaction",
        state_fips: str,
        county_fips: str,
        year: str = "2023",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        sf, err = _normalize_state_fips(state_fips)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        cf, err = _normalize_county_fips(county_fips)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        # ACS bounds: keep permissive but not absurd/future-proof. (Backend can still reject if unavailable.)
        y = _normalize_year(year, default=2023, min_year=2005, max_year=2100)

        params = {"state_fips": sf, "county_fips": cf, "year": str(y)}
        code, text, data = await api_request(
            api,
            "GET",
            "/external/census/county_population",
            params=params,
            timeout=20,
        )

        # Graceful if endpoint not shipped yet
        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Census proxy not available yet (endpoint pending: `/external/census/county_population`).",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        name = _safe_str(data.get("name"), 120) or f"FIPS {sf}-{cf}"
        yr = _safe_str(data.get("year"), 16) or str(y)
        pop = data.get("total_population")

        msg = f"🏛️ Census ACS {yr}\n{name}\nTotal population: {pop}"
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="bls",
        description="BLS lookup: series data. Requires BLS_API_KEY in backend env.",
    )
    @app_commands.describe(
        series_id="BLS series id, e.g., LAUCN050010000000003",
        start_year="default 2022",
        end_year="default 2025",
    )
    async def bls(
        interaction: "discord.Interaction",
        series_id: str,
        start_year: str = "2022",
        end_year: str = "2025",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        sid, sid_err = _normalize_series_id(series_id)
        if sid_err:
            await interaction.followup.send(sid_err, ephemeral=True)
            return

        sy = _normalize_year(start_year, default=2022, min_year=1900, max_year=2100)
        ey = _normalize_year(end_year, default=2025, min_year=1900, max_year=2100)

        # If user flips them, auto-fix
        if ey < sy:
            sy, ey = ey, sy

        params = {"series_id": sid, "start_year": str(sy), "end_year": str(ey)}
        code, text, data = await api_request(
            api,
            "GET",
            "/external/bls/series",
            params=params,
            timeout=30,
        )

        # Graceful if endpoint not shipped yet
        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ BLS proxy not available yet (endpoint pending: `/external/bls/series`).",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        # Backend returns: {"results": {...}} (mirrors BLS API-ish shape)
        results = data.get("results")
        if not isinstance(results, dict):
            await interaction.followup.send("📊 BLS: response received but missing `results`.", ephemeral=True)
            return

        title = _safe_str(results.get("seriesID"), 80) or sid
        points = results.get("data") if isinstance(results.get("data"), list) else []
        shown = points[:5]

        lines: List[str] = []
        for p in shown:
            if not isinstance(p, dict):
                continue
            yr = _safe_str(p.get("year"), 8)
            per = _safe_str(p.get("periodName"), 24)
            val = _safe_str(p.get("value"), 24)
            if yr or per or val:
                lines.append(f"{yr}-{per}: {val}".strip("-: "))

        msg = "📊 BLS series\n" f"{title}\n" + ("\n".join(lines) if lines else "(no data)")
        if len(points) > 5:
            msg += "\n…(showing 5 points)"

        await interaction.followup.send(msg, ephemeral=True)
