from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional, Any, Dict, Tuple, List

import httpx
import discord
from discord import app_commands

from ..config import settings

# Prefer env override for flexibility (local vs hosted)
API_BASE = os.getenv("DASHBOARD_API_BASE", "http://127.0.0.1:8000").rstrip("/")

# If set, sync slash commands only to this guild for near-instant updates (recommended for beta)
DISCORD_GUILD_ID_RAW = os.getenv("DISCORD_GUILD_ID")

# Where humans should post wins (routing cue; rename channel in Discord and update here if needed)
WINS_CHANNEL_NAME = os.getenv("DASHBOARD_WINS_CHANNEL", "wins-and-updates")
FIRST_ACTIONS_CHANNEL_NAME = os.getenv("DASHBOARD_FIRST_ACTIONS_CHANNEL", "first-actions")

# HTTP defaults
DEFAULT_TIMEOUT_S = float(os.getenv("DASHBOARD_HTTP_TIMEOUT", "20"))
DEFAULT_UA = "campaign-dashboard-discord-bot/1.0"


# -----------------------------
# Helpers
# -----------------------------

def _safe_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        return int(s.strip())
    except Exception:
        return None


DISCORD_GUILD_ID: Optional[int] = _safe_int(DISCORD_GUILD_ID_RAW)


def _parse_iso_dt(s: Optional[str]) -> Tuple[Optional[datetime], bool]:
    """
    Accepts ISO strings like:
      2025-12-29T00:00:00
      2025-12-29
    Returns (naive_datetime_or_none, parsed_ok).

    NOTE: We keep naive datetimes here because the API currently accepts naive ISO
    and normalizes server-side. If you standardize on tz-aware later, update here too.
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


def _truncate(s: str, limit: int = 1500) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _infer_channel_from_action_type(action_type: str) -> str:
    """
    Maps your action types to a broad reporting channel.
    """
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


def _wins_hint() -> str:
    return f"👉 After you take action, drop a ✅ in **#{WINS_CHANNEL_NAME}** so we can celebrate you."


def _next_step_for_stage(stage: Optional[str]) -> str:
    """
    Simple routing logic for the 7-day arc + gated stages.
    """
    s = (stage or "").lower()
    if s in ("observer", "new", ""):
        return f"Welcome! Your first step is to do **one small action** today. {_wins_hint()}"
    if s == "active":
        return f"You're ACTIVE 🎉 Do one more action today (or help someone else start). {_wins_hint()}"
    if s == "owner":
        return f"You're OWNER-level momentum 💪 Pick a lane and help onboard 1 person this week. {_wins_hint()}"

    # gated / elevated stages
    if s == "team":
        return f"You're TEAM-approved ✅ Coordinate with your lead and keep logging wins. {_wins_hint()}"
    if s == "fundraising":
        return f"You're FUNDRAISING-approved 💸 Follow your fundraising lane plan and log each touch. {_wins_hint()}"
    if s == "leader":
        return f"You're LEADER-level ⭐ Onboard 1 person this week and keep the cadence. {_wins_hint()}"

    return f"You're in **{stage}**. Keep logging wins and supporting others. {_wins_hint()}"


def _clamp_quantity(qty: int) -> Tuple[int, Optional[str]]:
    """
    Prevent garbage quantities while still being friendly.
    Returns (qty_clamped, warning_message_or_none).
    """
    if qty < 1:
        return 1, "Quantity must be >= 1. I logged it as 1."
    if qty > 10000:
        return 10000, "Quantity was very large. I capped it at 10,000."
    return qty, None


def _normalize_request_type(rt: str) -> Optional[str]:
    """
    Accepts a few friendly variants and returns API enum values:
      - team -> team_access
      - fundraising -> fundraising_access
    """
    s = (rt or "").strip().lower()
    if s in ("team", "team_access"):
        return "team_access"
    if s in ("fundraising", "fundraising_access"):
        return "fundraising_access"
    return None


def _format_api_error(code: int, text: str, data: Optional[dict]) -> str:
    """
    Render an API error clearly. Prefer FastAPI {detail: "..."} if present.
    """
    detail = None
    if isinstance(data, dict):
        detail = data.get("detail")
    if detail:
        return f"❌ Error ({code}): {detail}"
    return f"❌ Error ({code}): {_truncate(text)}"


async def _api_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Tuple[int, str, Optional[dict]]:
    """
    Shared API wrapper: returns (status_code, raw_text, json_dict_or_none).
    Never raises on network errors — Discord handlers should not crash the bot.
    """
    url = f"{API_BASE}{path}"
    try:
        r = await client.request(method, url, params=params, json=json, timeout=timeout)
    except httpx.TimeoutException:
        return 408, "Request timed out contacting API.", None
    except httpx.RequestError as e:
        return 503, f"Network error contacting API: {e}", None
    except Exception as e:
        return 500, f"Unexpected error contacting API: {e}", None

    text = r.text
    data: Optional[dict] = None
    try:
        data = r.json()
    except Exception:
        data = None
    return r.status_code, text, data


# -----------------------------
# Bot client
# -----------------------------

class DashboardBot(discord.Client):
    """
    IMPORTANT:
    - discord.Client already uses self.http internally (Discord HTTP client).
    - Do NOT overwrite self.http.
    We keep our own httpx client as self.api.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

        # Our API HTTP client (httpx). Name MUST NOT be "http".
        self.api: Optional[httpx.AsyncClient] = None

    async def setup_hook(self) -> None:
        # Create pooled httpx client for the life of the bot process
        if self.api is None:
            self.api = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT_S,
                headers={"User-Agent": DEFAULT_UA},
            )

        # For beta iteration: guild-only sync is MUCH faster.
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        # Close pooled httpx resources
        if self.api is not None:
            try:
                await self.api.aclose()
            except Exception:
                pass
            self.api = None
        await super().close()


bot = DashboardBot()


# -----------------------------
# Core sanity + config
# -----------------------------

@bot.tree.command(name="ping", description="Sanity check: bot is alive.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"✅ Pong. Bot is online.\nAPI: {API_BASE}\nGuild sync: {'ON' if DISCORD_GUILD_ID else 'OFF'}",
        ephemeral=True,
    )


@bot.tree.command(name="config", description="Show bot configuration (API base + guild sync).")
async def config_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "⚙️ Team Hub Bot Config\n"
        f"- API_BASE: {API_BASE}\n"
        f"- DISCORD_GUILD_ID: {DISCORD_GUILD_ID or '(global sync)'}\n"
        f"- WINS_CHANNEL: #{WINS_CHANNEL_NAME}\n"
        f"- FIRST_ACTIONS_CHANNEL: #{FIRST_ACTIONS_CHANNEL_NAME}",
        ephemeral=True,
    )


# -----------------------------
# Setup helper (optional)
# -----------------------------

@bot.tree.command(name="setup", description="One-shot: bootstrap rules + create a beta Power of 5 team.")
@app_commands.describe(
    leader_name="Leader name to create/use",
    leader_email="Optional email (used to reuse existing leader person)",
    leader_phone="Optional phone (used to reuse existing leader person)",
    team_name="Optional team name",
)
async def setup(
    interaction: discord.Interaction,
    leader_name: str = "Beta Leader",
    leader_email: Optional[str] = None,
    leader_phone: Optional[str] = None,
    team_name: str = "Power of 5 (Beta)",
):
    """
    Requires API router: /bootstrap/rules and /bootstrap/power5_team
    """
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    # 1) Bootstrap impact rules
    code, text, data = await _api_request(bot.api, "POST", "/bootstrap/rules", timeout=30)
    if code != 200:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    # 2) Bootstrap leader + team
    params: Dict[str, Any] = {"leader_name": leader_name, "team_name": team_name}
    if leader_email:
        params["leader_email"] = leader_email
    if leader_phone:
        params["leader_phone"] = leader_phone

    code, text, data = await _api_request(bot.api, "POST", "/bootstrap/power5_team", params=params, timeout=30)
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    team_id = data["power_team_id"]
    leader_id = data["leader_person_id"]
    leader_tn = data.get("leader_tracking_number")

    msg = (
        "✅ Setup complete.\n"
        f"- leader_person_id: {leader_id}\n"
        f"- leader_tracking_number: {leader_tn}\n"
        f"- power_team_id: {team_id}\n\n"
        "Try next:\n"
        f"1) /log action_type:call quantity:10 actor_person_id:{leader_id} team_id:{team_id}\n"
        f"2) /reach team_id:{team_id}\n"
        f"3) /p5_stats team_id:{team_id}\n"
        f"4) /p5_tree team_id:{team_id}\n"
    )
    await interaction.followup.send(msg, ephemeral=True)


# -----------------------------
# POWER OF 5
# -----------------------------

@bot.tree.command(name="p5_stats", description="Power of 5: show team stats (counts by depth/status).")
@app_commands.describe(team_id="power_team_id (integer)")
async def p5_stats(interaction: discord.Interaction, team_id: int):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    code, text, data = await _api_request(bot.api, "GET", f"/power5/teams/{team_id}/stats", timeout=15)
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    by_status = data.get("by_status", {}) or {}
    by_depth = data.get("by_depth", {}) or {}

    status_lines = [f"- {k}: {v}" for k, v in sorted(by_status.items(), key=lambda kv: kv[0])]
    depth_lines = [f"- depth {k}: {v}" for k, v in sorted(by_depth.items(), key=lambda kv: int(kv[0]))]

    msg = (
        f"🌟 Power of 5 stats — team_id={team_id}\n"
        f"Leader person_id: {data.get('leader_person_id')}\n"
        f"Links total: {data.get('links_total')}\n\n"
        "Status counts:\n"
        + ("\n".join(status_lines) if status_lines else "- (none)")
        + "\n\nDepth counts:\n"
        + ("\n".join(depth_lines) if depth_lines else "- (none)")
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="p5_invite", description="Power of 5: create an onboarding invite (returns token once).")
@app_commands.describe(
    team_id="power_team_id (integer)",
    invited_by_person_id="person_id who is inviting",
    channel="email|sms|discord",
    destination="email address or phone number or discord handle",
    invitee_person_id="optional existing person_id for the invitee",
)
async def p5_invite(
    interaction: discord.Interaction,
    team_id: int,
    invited_by_person_id: int,
    channel: str,
    destination: str,
    invitee_person_id: Optional[int] = None,
):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    params: Dict[str, Any] = {
        "invited_by_person_id": invited_by_person_id,
        "channel": channel,
        "destination": destination,
    }
    if invitee_person_id is not None:
        params["invitee_person_id"] = invitee_person_id

    code, text, data = await _api_request(
        bot.api,
        "POST",
        f"/power5/teams/{team_id}/invites",
        params=params,
        timeout=20,
    )
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    token = data.get("token")
    expires_at = data.get("expires_at")

    msg = (
        f"✅ Invite created — team_id={team_id}\n"
        f"Channel: {channel}\n"
        f"Destination: {destination}\n"
        f"Expires: {expires_at}\n\n"
        f"🔑 Token (showing once):\n`{token}`\n\n"
        "Later: user posts token to your web form or bot consumes it via /power5/invites/consume."
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="p5_link", description="Power of 5: link recruiter -> recruit inside a team (creates/updates a link).")
@app_commands.describe(
    team_id="power_team_id",
    parent_person_id="recruiter person_id",
    child_person_id="recruit person_id",
    status="invited|onboarded|active|churned (default invited)",
)
async def p5_link(
    interaction: discord.Interaction,
    team_id: int,
    parent_person_id: int,
    child_person_id: int,
    status: str = "invited",
):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    payload = {
        "power_team_id": team_id,
        "parent_person_id": parent_person_id,
        "child_person_id": child_person_id,
        "status": status,
    }

    code, text, data = await _api_request(
        bot.api,
        "POST",
        f"/power5/teams/{team_id}/links",
        json=payload,
        timeout=20,
    )
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    msg = (
        "✅ Power of 5 link saved\n"
        f"- team_id: {data.get('power_team_id')}\n"
        f"- parent_person_id: {data.get('parent_person_id')}\n"
        f"- child_person_id: {data.get('child_person_id')}\n"
        f"- depth: {data.get('depth')}\n"
        f"- status: {data.get('status')}"
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="p5_tree", description="Power of 5: show simple tree adjacency (compact).")
@app_commands.describe(team_id="power_team_id")
async def p5_tree(interaction: discord.Interaction, team_id: int):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    code, text, data = await _api_request(bot.api, "GET", f"/power5/teams/{team_id}/tree", timeout=20)
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    children = data.get("children", {}) or {}
    leader_id = data.get("leader_person_id")

    lines: List[str] = [f"Leader: {leader_id}"]
    shown = 0
    for parent, kids in children.items():
        if shown > 30:
            lines.append("…(truncated)")
            break
        kid_parts = [f"{k.get('child_person_id')} (d{k.get('depth')},{k.get('status')})" for k in kids]
        lines.append(f"{parent} -> " + ", ".join(kid_parts))
        shown += 1

    await interaction.followup.send("🌳 Power of 5 Tree\n" + "\n".join(lines), ephemeral=True)


# -----------------------------
# IMPACT (wins logging)
# -----------------------------

@bot.tree.command(name="log", description="Log an impact action (call/text/door/event/etc).")
@app_commands.describe(
    action_type="e.g., call, text, door, event_hosted, event_attended, post_shared, signup",
    quantity="how many (default 1)",
    actor_person_id="optional person_id who did it",
    team_id="optional power_team_id",
    county_id="optional county_id",
    occurred_at="optional ISO datetime or YYYY-MM-DD (default now)",
    note="optional note (stored in meta)",
)
async def log_action(
    interaction: discord.Interaction,
    action_type: str,
    quantity: int = 1,
    actor_person_id: Optional[int] = None,
    team_id: Optional[int] = None,
    county_id: Optional[int] = None,
    occurred_at: Optional[str] = None,
    note: Optional[str] = None,
):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    dt, dt_ok = _parse_iso_dt(occurred_at)
    qty, qty_warn = _clamp_quantity(quantity)

    # Idempotency: stable per interaction (prevents duplicates on retries)
    guild_id = interaction.guild_id
    idem = f"discord:{guild_id}:{interaction.id}"

    meta: Dict[str, Any] = {
        "discord": {
            "guild_id": str(guild_id) if guild_id else None,
            "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
            "user_id": str(interaction.user.id) if interaction.user else None,
            "username": str(interaction.user) if interaction.user else None,
            "interaction_id": str(interaction.id),
        }
    }
    if note:
        meta["note"] = note

    payload: Dict[str, Any] = {
        "action_type": action_type,
        "quantity": qty,
        "actor_person_id": actor_person_id,
        "power_team_id": team_id,
        "county_id": county_id,
        "occurred_at": (dt.isoformat() if dt else None),
        "source": "discord",
        "channel": _infer_channel_from_action_type(action_type),
        "idempotency_key": idem,
        "meta": meta,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    code, text, data = await _api_request(bot.api, "POST", "/impact/actions", json=payload, timeout=25)
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    stage_changed_to = data.get("stage_changed_to")
    actor_stage = data.get("actor_stage")

    header = "✅ Logged impact action"
    warnings: List[str] = []
    if occurred_at and not dt_ok:
        warnings.append("⚠️ I couldn't parse your occurred_at date/time. Logged it as *now*.")
    if qty_warn:
        warnings.append(f"⚠️ {qty_warn}")

    msg = (
        f"{header}\n"
        f"- type: {data.get('action_type')}\n"
        f"- qty: {data.get('quantity')}\n"
        f"- actor_person_id: {data.get('actor_person_id')}\n"
        f"- power_team_id: {data.get('power_team_id')}\n"
        f"- county_id: {data.get('county_id')}\n"
        f"- occurred_at: {data.get('occurred_at')}\n"
    )

    if warnings:
        msg = msg + "\n" + "\n".join(warnings) + "\n"

    # Celebrate a stage change (routing cue)
    if stage_changed_to:
        msg += f"\n🎉 Stage updated: **{str(stage_changed_to).upper()}**\n"
        msg += _wins_hint()
    else:
        msg += "\n" + _wins_hint()

    # Add “next step” hint when available
    if actor_stage:
        msg += "\n\nNext step:\n" + _next_step_for_stage(actor_stage)

    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="reach", description="Compute impact reach summary over a date range (uses /impact/reach/summary).")
@app_commands.describe(
    start="optional ISO datetime or YYYY-MM-DD (inclusive)",
    end="optional ISO datetime or YYYY-MM-DD (exclusive)",
    actor_person_id="optional filter",
    team_id="optional filter",
    county_id="optional filter",
)
async def reach(
    interaction: discord.Interaction,
    start: Optional[str] = None,
    end: Optional[str] = None,
    actor_person_id: Optional[int] = None,
    team_id: Optional[int] = None,
    county_id: Optional[int] = None,
):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    start_dt, start_ok = _parse_iso_dt(start)
    end_dt, end_ok = _parse_iso_dt(end)

    if (start and not start_ok) or (end and not end_ok):
        warn = "⚠️ I couldn't parse your date filter(s). "
        if start and not start_ok:
            warn += "Start ignored. "
        if end and not end_ok:
            warn += "End ignored. "
        await interaction.followup.send(warn.strip(), ephemeral=True)

    params: Dict[str, Any] = {}
    if start_dt:
        params["start"] = start_dt.isoformat()
    if end_dt:
        params["end"] = end_dt.isoformat()
    if actor_person_id is not None:
        params["actor_person_id"] = actor_person_id
    if team_id is not None:
        params["power_team_id"] = team_id
    if county_id is not None:
        params["county_id"] = county_id

    code, text, data = await _api_request(bot.api, "GET", "/impact/reach/summary", params=params, timeout=20)
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    qty_by_type = data.get("quantity_by_type", {}) or {}
    lines = [f"- {k}: {v}" for k, v in sorted(qty_by_type.items(), key=lambda kv: kv[0])]

    msg = (
        "📈 Impact Reach Summary\n"
        f"Computed reach: {data.get('computed_reach')}\n"
        f"Actions rows: {data.get('actions_total')}\n"
        f"Rules loaded: {data.get('rules_loaded')}\n\n"
        "Quantities by type:\n"
        + ("\n".join(lines) if lines else "- (none)")
    )
    await interaction.followup.send(msg, ephemeral=True)


# -----------------------------
# Volunteer routing (7-day arc helper)
# -----------------------------

@bot.tree.command(name="my_next", description="Get your next suggested step in the 7-day activation arc.")
@app_commands.describe(actor_stage="Optional: your current stage if you know it (observer/new/active/owner/team/fundraising/leader).")
async def my_next(interaction: discord.Interaction, actor_stage: Optional[str] = None):
    await interaction.response.send_message(_next_step_for_stage(actor_stage), ephemeral=True)


# -----------------------------
# Approvals (TEAM/FUNDRAISING gating)
# -----------------------------

@bot.tree.command(
    name="request_team_access",
    description="Request human-approved access (TEAM or FUNDRAISING).",
)
@app_commands.describe(
    request_type="team|fundraising",
    notes="Optional: what access you need + why (short).",
)
async def request_team_access(
    interaction: discord.Interaction,
    request_type: str = "team",
    notes: Optional[str] = None,
):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    api_rt = _normalize_request_type(request_type)
    if not api_rt:
        await interaction.followup.send("❌ request_type must be `team` or `fundraising`.", ephemeral=True)
        return

    payload = {
        "discord_user_id": str(interaction.user.id),
        "name": interaction.user.display_name,
        "request_type": api_rt,
        "notes": notes,
    }

    code, text, data = await _api_request(bot.api, "POST", "/approvals/request", json=payload, timeout=20)

    if code == 404:
        await interaction.followup.send(
            "✅ Request noted — approvals endpoint not live yet.\n"
            "Next dev step: wire /approvals router into the API and restart the API service.",
            ephemeral=True,
        )
        return

    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    approval_id = data.get("id")
    req_type = data.get("request_type")
    status = data.get("status")

    await interaction.followup.send(
        "✅ Request submitted.\n"
        f"- approval_id: {approval_id}\n"
        f"- request_type: {req_type}\n"
        f"- status: {status}\n\n"
        "A campaign admin will review it shortly.\n"
        "Tip: you can keep logging wins while you wait. " + _wins_hint(),
        ephemeral=True,
    )


# -----------------------------
# External lookups
# -----------------------------

@bot.tree.command(name="census", description="Census lookup: county population (ACS). Requires CENSUS_API_KEY in .env.")
@app_commands.describe(
    state_fips="State FIPS (AR = 05)",
    county_fips="County FIPS (3 digits, e.g., Pulaski = 119)",
    year="ACS year, default 2023",
)
async def census(interaction: discord.Interaction, state_fips: str, county_fips: str, year: str = "2023"):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    code, text, data = await _api_request(
        bot.api,
        "GET",
        "/external/census/county_population",
        params={"state_fips": state_fips, "county_fips": county_fips, "year": year},
        timeout=20,
    )
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    await interaction.followup.send(
        f"🏛️ Census ACS {data['year']}\n{data['name']}\nTotal population: {data['total_population']}",
        ephemeral=True,
    )


@bot.tree.command(name="bls", description="BLS lookup: series data. Requires BLS_API_KEY in .env.")
@app_commands.describe(series_id="BLS series id, e.g., LAUCN050010000000003")
async def bls(interaction: discord.Interaction, series_id: str, start_year: str = "2022", end_year: str = "2025"):
    await interaction.response.defer(ephemeral=True)

    if bot.api is None:
        await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
        return

    code, text, data = await _api_request(
        bot.api,
        "GET",
        "/external/bls/series",
        params={"series_id": series_id, "start_year": start_year, "end_year": end_year},
        timeout=30,
    )
    if code != 200 or not data:
        await interaction.followup.send(_format_api_error(code, text, data), ephemeral=True)
        return

    s = data["results"]
    title = s.get("seriesID", series_id)
    points = s.get("data", [])[:5]
    lines = [f"{p.get('year')}-{p.get('periodName')}: {p.get('value')}" for p in points]

    await interaction.followup.send(
        "📊 BLS series\n"
        f"{title}\n"
        + "\n".join(lines)
        + ("\n…(showing 5 points)" if len(points) == 5 else ""),
        ephemeral=True,
    )


def run_bot() -> None:
    logging.basicConfig(level=getattr(logging, str(settings.log_level).upper(), logging.INFO))
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in .env")
    bot.run(settings.discord_bot_token)


if __name__ == "__main__":
    run_bot()
