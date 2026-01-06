from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple

import discord
from discord import app_commands

from ...config.settings import settings
from .shared import split_csv

if TYPE_CHECKING:
    from discord import Interaction


def _normalize_name(s: str) -> str:
    return (s or "").strip().lower()


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _parse_channel_ref(raw: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Accept:
      - numeric channel id
      - channel name (no #)
      - "#channel-name"
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.startswith("#"):
        s = s[1:].strip()
    if s.isdigit():
        try:
            return int(s), None
        except Exception:
            return None, None
    return None, s


def _member_has_any_role(member: discord.abc.User, role_specs: list[str]) -> bool:
    """
    Simple role check for this module. (Approvals has the fully-hardened parser.)

    Fail-closed:
      - returns False if not a guild Member or if role_specs empty
      - supports role IDs and role names (case-insensitive)
    """
    if not role_specs:
        return False
    if not isinstance(member, discord.Member):
        return False

    role_ids: set[int] = set()
    role_names: set[str] = set()

    for spec in role_specs:
        raw = (spec or "").strip()
        if not raw:
            continue
        if raw.isdigit():
            try:
                role_ids.add(int(raw))
            except Exception:
                continue
        else:
            role_names.add(_normalize_name(raw))

    for r in getattr(member, "roles", []) or []:
        try:
            rid = int(getattr(r, "id", 0) or 0)
            rname = _normalize_name(getattr(r, "name", "") or "")
            if role_ids and rid in role_ids:
                return True
            if role_names and rname in role_names:
                return True
        except Exception:
            continue

    return False


def _is_admin(interaction: "Interaction") -> bool:
    """
    Admin guard (fail-closed):

    - Must be invoked in a guild by a Member.
    - If DASHBOARD_ADMIN_ROLES configured: role-based ONLY.
    - Else fallback: Manage Guild or Administrator permission.
    """
    guild = interaction.guild
    u = interaction.user

    if guild is None:
        return False
    if not isinstance(u, discord.Member):
        return False

    admin_roles = split_csv(settings.admin_roles_raw)
    if admin_roles:
        return _member_has_any_role(u, admin_roles)

    perms = u.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def _guard(check_fn: Callable[[Any], bool], fail_msg: str):
    async def predicate(interaction: "Interaction") -> bool:
        ok = False
        try:
            ok = bool(check_fn(interaction))
        except Exception:
            ok = False

        if ok:
            return True

        try:
            if interaction.response.is_done():
                await interaction.followup.send(fail_msg, ephemeral=True)
            else:
                await interaction.response.send_message(fail_msg, ephemeral=True)
        except Exception:
            pass
        return False

    return app_commands.check(predicate)


def _feature_flags_summary() -> str:
    emoji = (settings.wins_trigger_emoji or "").strip() or "(not set)"
    parts = [
        f"WINS_AUTOMATION={'ON' if settings.enable_wins_automation else 'OFF'}",
        f"ROLE_SYNC={'ON' if settings.enable_role_sync else 'OFF'}",
        f"TRAINING_SYSTEM={'ON' if settings.enable_training_system else 'OFF'}",
        f"TRIGGER_EMOJI={emoji}",
    ]
    return ", ".join(parts)


def _wins_bundle_summary() -> str:
    """
    These are read by the bot process (env-level toggles).
    We show them in /config for operator clarity.
    """
    react_on = _env_bool("DASHBOARD_WINS_REACT", True)
    party_on = _env_bool("DASHBOARD_WINS_REACT_PARTY", True)
    reply_on = _env_bool("DASHBOARD_WINS_REPLY", True)
    autolog_on = _env_bool("DASHBOARD_WINS_AUTOLOG", True)
    forward_on = _env_bool("DASHBOARD_WINS_FORWARD", True)
    forward_raw = _env("DASHBOARD_WINS_FORWARD_CHANNEL", "").strip()
    _, forward_name = _parse_channel_ref(forward_raw)

    forward_label = "(not set)"
    if forward_raw:
        if forward_raw.strip().lstrip("#").isdigit():
            forward_label = f"(id) {forward_raw.strip()}"
        else:
            forward_label = f"#{(forward_name or forward_raw).strip().lstrip('#')}"

    return (
        f"REACT={'ON' if react_on else 'OFF'}, "
        f"PARTY={'ON' if party_on else 'OFF'}, "
        f"REPLY={'ON' if reply_on else 'OFF'}, "
        f"AUTOLOG={'ON' if autolog_on else 'OFF'}, "
        f"FORWARD={'ON' if forward_on else 'OFF'} -> {forward_label}"
    )


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Core sanity + config commands.

    Keep this module intentionally small and stable:
      - /ping      sanity check
      - /wins_help volunteer instructions for ✅ wins
      - /config    admin: show bot configuration (safe values only)
    """

    @tree.command(name="ping", description="Sanity check: bot is alive.")
    async def ping(interaction: "discord.Interaction") -> None:
        api_base = settings.dashboard_api_base.rstrip("/")
        guild_id: Optional[int] = settings.discord_guild_id

        await interaction.response.send_message(
            "✅ Pong. Bot is online.\n"
            f"API: {api_base}\n"
            f"Guild sync: {'ON' if guild_id else 'OFF (global)'}\n"
            f"Features: {_feature_flags_summary()}",
            ephemeral=True,
        )

    @tree.command(name="wins_help", description="How to post wins so the bot auto-reacts, logs, and routes them.")
    async def wins_help(interaction: "discord.Interaction") -> None:
        trigger = (settings.wins_trigger_emoji or "✅").strip() or "✅"
        wins_chan = (settings.wins_channel_name or "wins-and-updates").strip()

        msg = (
            "🏁 **How to post a win (so it auto-runs):**\n"
            f"1) Go to **#{wins_chan}**\n"
            f"2) Post your win message **including the emoji** `{trigger}` in the message text.\n"
            "   Example: `✅ I made 15 calls today!`\n"
            "\n"
            "**Important:** A *reaction-only* ✅ does **not** trigger automation (the bot watches message text).\n"
            "\n"
            "After you post, the bot will:\n"
            "- react ✅ (and 🎉)\n"
            "- reply with a short `/log ...` suggestion\n"
            "- auto-log into the dashboard (best-effort)\n"
            "- forward to the leader channel (if configured)\n"
        )

        await interaction.response.send_message(msg, ephemeral=True)

    @tree.command(name="config", description="Admin: show bot configuration (API base + guild sync).")
    @_guard(_is_admin, "❌ Admin only. You need a configured admin role or Manage Server permission.")
    async def config_cmd(interaction: "discord.Interaction") -> None:
        api_base = settings.dashboard_api_base.rstrip("/")
        guild_id: Optional[int] = settings.discord_guild_id

        admin_roles = split_csv(settings.admin_roles_raw)
        lead_roles = split_csv(settings.lead_roles_raw)

        # Avoid leaking sensitive config (tokens/keys). Only show safe operational values.
        await interaction.response.send_message(
            "⚙️ Team Hub Bot Config\n"
            f"- API_BASE: {api_base}\n"
            f"- DISCORD_GUILD_ID: {guild_id or '(global sync)'}\n"
            f"- WINS_CHANNEL: #{settings.wins_channel_name}\n"
            f"- FIRST_ACTIONS_CHANNEL: #{settings.first_actions_channel_name}\n"
            f"- ADMIN_ROLES: {', '.join(admin_roles) if admin_roles else '(permission-based)'}\n"
            f"- LEAD_ROLES: {', '.join(lead_roles) if lead_roles else '(none)'}\n"
            f"- TEAM_ROLE_NAME: {settings.role_team}\n"
            f"- FUNDRAISING_ROLE_NAME: {settings.role_fundraising}\n"
            f"- LEADER_ROLE_NAME: {settings.role_leader}\n"
            f"- ONBOARDING_URL: {settings.onboarding_url or '(not set)'}\n"
            f"- VOLUNTEER_FORM_URL: {settings.volunteer_form_url or '(not set)'}\n"
            f"- DISCORD_HELP_URL: {settings.discord_help_url or '(not set)'}\n"
            f"- FEATURES: {_feature_flags_summary()}\n"
            f"- WINS_PIPELINE: {_wins_bundle_summary()}",
            ephemeral=True,
        )
