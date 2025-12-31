from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

import httpx

from ...config.settings import settings

logger = logging.getLogger(__name__)

# NOTE:
# Keep this module dependency-light (no discord import).
# This file is the single source of truth for bot->backend HTTP behavior.

# Stable API base (evaluated at import time from env-backed settings)
API_BASE = settings.dashboard_api_base.rstrip("/")

# HTTP defaults
DEFAULT_TIMEOUT_S = float(settings.http_timeout_s)
DEFAULT_UA = settings.http_user_agent


# -----------------------------
# Typing helpers (no discord import)
# -----------------------------

@runtime_checkable
class _HasApi(Protocol):
    api: Optional[httpx.AsyncClient]


def _get_api_client(bot_or_api: Union[httpx.AsyncClient, Any]) -> Optional[httpx.AsyncClient]:
    """
    Accepts either:
      - httpx.AsyncClient (legacy call sites)
      - bot-like object with `.api` attribute (preferred)
    Returns the httpx client or None.
    """
    if isinstance(bot_or_api, httpx.AsyncClient):
        return bot_or_api

    try:
        api = getattr(bot_or_api, "api", None)
        if isinstance(api, httpx.AsyncClient):
            return api
    except Exception:
        return None

    return None


# -----------------------------
# Small primitives
# -----------------------------

def split_csv(raw: str) -> List[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def parse_iso_dt(s: Optional[str]) -> Tuple[Optional[datetime], bool]:
    """
    Accepts ISO strings like:
      2025-12-29T00:00:00
      2025-12-29
    Returns (naive_datetime_or_none, parsed_ok).
    """
    if not s:
        return None, True
    s = s.strip()
    try:
        if len(s) == 10:  # YYYY-MM-DD
            return datetime.fromisoformat(s + "T00:00:00"), True
        return datetime.fromisoformat(s), True
    except Exception:
        return None, False


def truncate(s: str, limit: int = 1500) -> str:
    if not s:
        return ""
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def infer_channel_from_action_type(action_type: str) -> str:
    a = (action_type or "").strip().lower()
    if a in ("call", "calls"):
        return "call"
    if a in ("text", "texts"):
        return "text"
    if a in ("door", "doors", "knock"):
        return "door"
    if a.startswith("event_") or a in ("event", "rally", "meeting"):
        return "event"
    if a in ("post_shared", "share", "social", "post"):
        return "social"
    return "other"


def wins_hint() -> str:
    return (
        f"👉 After you take action, drop a {settings.wins_trigger_emoji} "
        f"in **#{settings.wins_channel_name}** so we can celebrate you."
    )


def first_actions_hint() -> str:
    return f"👉 Need ideas? Check **#{settings.first_actions_channel_name}** for your first action menu."


def next_step_for_stage(stage: Optional[str]) -> str:
    s = (stage or "").lower()
    if s in ("observer", "new", ""):
        return (
            "Welcome! Your first step is to do **one small action** today.\n"
            f"{first_actions_hint()}\n"
            f"{wins_hint()}"
        )
    if s == "active":
        return f"You're ACTIVE 🎉 Do one more action today (or help someone else start).\n{wins_hint()}"
    if s == "owner":
        return f"You're OWNER-level momentum 💪 Pick a lane and onboard 1 person this week.\n{wins_hint()}"

    if s == "team":
        return f"You're TEAM-approved ✅ Coordinate with your lead and keep logging wins.\n{wins_hint()}"
    if s == "fundraising":
        return f"You're FUNDRAISING-approved 💸 Follow your fundraising lane plan and log each touch.\n{wins_hint()}"
    if s == "leader":
        return f"You're LEADER-level ⭐ Onboard 1 person this week and keep the cadence.\n{wins_hint()}"

    return f"You're in **{stage}**. Keep logging wins and supporting others.\n{wins_hint()}"


def clamp_quantity(qty: int) -> Tuple[int, Optional[str]]:
    if qty < 1:
        return 1, "Quantity must be >= 1. I logged it as 1."
    if qty > 10000:
        return 10000, "Quantity was very large. I capped it at 10,000."
    return qty, None


def format_api_error(code: int, text: str, data: Optional[dict]) -> str:
    detail = None
    if isinstance(data, dict):
        detail = data.get("detail")
    if detail:
        return f"❌ Error ({code}): {detail}"
    return f"❌ Error ({code}): {truncate(text)}"


# -----------------------------
# API helpers
# -----------------------------

def _safe_json(r: httpx.Response) -> Optional[dict]:
    """
    Best-effort JSON parse:
      - returns dict if payload is a dict
      - returns None if not JSON or not dict
    """
    try:
        payload = r.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


async def api_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Tuple[int, str, Optional[dict]]:
    """
    Low-level request helper used by all command modules.
    Returns: (status_code, response_text, json_dict_or_none)
    """
    m = (method or "GET").strip().upper()
    p = path if (path or "").startswith("/") else f"/{path}"
    url = f"{API_BASE}{p}"

    try:
        r = await client.request(m, url, params=params, json=json, timeout=float(timeout))
    except httpx.TimeoutException:
        return 408, "Request timed out contacting API.", None
    except httpx.RequestError as e:
        return 503, f"Network error contacting API: {e}", None
    except Exception as e:
        logger.exception("Unexpected error contacting API (%s %s): %s", m, url, e)
        return 500, f"Unexpected error contacting API: {e}", None

    return r.status_code, r.text, _safe_json(r)


async def ensure_person_by_discord(
    bot_or_api: Union[httpx.AsyncClient, Any],
    interaction: Any,  # discord.Interaction (kept Any to avoid hard import)
) -> Tuple[Optional[int], Optional[dict], Optional[str]]:
    """
    Best-effort: ensure a Person exists for this discord user via /people/discord/upsert.

    Call styles supported:
      - ensure_person_by_discord(bot, interaction)         (preferred; bot has `.api`)
      - ensure_person_by_discord(api_client, interaction)  (legacy)

    Returns: (person_id, person_dict, error_msg_or_none)
    """
    api = _get_api_client(bot_or_api)
    if api is None:
        return None, None, "Bot API client is not initialized."

    payload: Dict[str, Any] = {
        "discord_user_id": str(interaction.user.id),
        "name": interaction.user.display_name,
    }

    code, text, data = await api_request(api, "POST", "/people/discord/upsert", json=payload, timeout=15)
    if code != 200 or not isinstance(data, dict):
        return None, None, format_api_error(code, text, data)

    pid = data.get("id")
    if isinstance(pid, int):
        return pid, data, None
    if isinstance(pid, str) and pid.isdigit():
        return int(pid), data, None

    return None, data, "⚠️ Upsert succeeded but returned no person id."


# -----------------------------
# Approvals helpers
# -----------------------------

def approval_type_from_user(rt: str) -> Optional[str]:
    s = (rt or "").strip().lower()
    if s in ("team", "team_access"):
        return "team_access"
    if s in ("fundraising", "fundraising_access", "fundraise"):
        return "fundraising_access"
    if s in ("leader", "lead", "leader_access"):
        return "leader_access"
    return None


def role_name_for_request_type(request_type: str) -> Optional[str]:
    rt = (request_type or "").strip().lower()
    if rt == "team_access":
        return settings.role_team
    if rt == "fundraising_access":
        return settings.role_fundraising
    if rt == "leader_access":
        return settings.role_leader
    return None


__all__ = [
    # constants
    "API_BASE",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_UA",
    # primitives
    "split_csv",
    "parse_iso_dt",
    "truncate",
    "infer_channel_from_action_type",
    "wins_hint",
    "first_actions_hint",
    "next_step_for_stage",
    "clamp_quantity",
    "format_api_error",
    # api
    "api_request",
    "ensure_person_by_discord",
    # approvals
    "approval_type_from_user",
    "role_name_for_request_type",
]
