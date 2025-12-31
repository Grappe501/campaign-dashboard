from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from discord import app_commands

from ...config.settings import settings
from .shared import split_csv

if TYPE_CHECKING:
    import discord


def _feature_flags_summary() -> str:
    emoji = (settings.wins_trigger_emoji or "").strip() or "(not set)"
    parts = [
        f"WINS_AUTOMATION={'ON' if settings.enable_wins_automation else 'OFF'}",
        f"ROLE_SYNC={'ON' if settings.enable_role_sync else 'OFF'}",
        f"TRIGGER_EMOJI={emoji}",
    ]
    return ", ".join(parts)


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Core sanity + config commands.

    Keep this module intentionally small and stable:
      - /ping   sanity check
      - /config show current bot configuration
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

    @tree.command(name="config", description="Show bot configuration (API base + guild sync).")
    async def config_cmd(interaction: "discord.Interaction") -> None:
        api_base = settings.dashboard_api_base.rstrip("/")
        guild_id: Optional[int] = settings.discord_guild_id

        admin_roles = split_csv(settings.admin_roles_raw)
        lead_roles = split_csv(settings.lead_roles_raw)

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
            f"- FEATURES: {_feature_flags_summary()}",
            ephemeral=True,
        )
