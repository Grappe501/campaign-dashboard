from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from .shared import ensure_person_by_discord, format_api_error
from ...config.settings import settings

if TYPE_CHECKING:
    from discord import Interaction

logger = logging.getLogger(__name__)


def _has_manage_roles(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(perms.manage_roles or perms.administrator)


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    /sync_me — Self-service role sync

    Purpose (Operator Readiness):
    - Allows a volunteer to sync their Discord roles with backend approvals
    - Safe to run repeatedly (idempotent)
    - Fail-soft with clear operator-visible logging

    Behavior:
    - Ensures Person exists for Discord user
    - Calls backend role-sync endpoint (if present)
    - Applies returned role changes in Discord
    """

    if not settings.enable_role_sync:
        logger.info("role_sync disabled via settings; /sync_me not registered")
        return

    @tree.command(name="sync_me", description="Sync your Discord roles with campaign approvals.")
    async def sync_me(interaction: "Interaction") -> None:
        # Must be run in a guild
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ This command can only be used inside the campaign server.",
                ephemeral=True,
            )
            return

        member: discord.Member = interaction.user

        # Bot must have permission to manage roles
        me = interaction.guild.me
        if me is None or not isinstance(me, discord.Member):
            await interaction.response.send_message(
                "❌ Bot permissions could not be verified.",
                ephemeral=True,
            )
            return

        if not _has_manage_roles(me):
            await interaction.response.send_message(
                "❌ Bot does not have permission to manage roles on this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Ensure person exists in backend
        person_id, person, err = await ensure_person_by_discord(bot, interaction)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        # Call backend role-sync endpoint (best-effort)
        if bot.api is None:
            await interaction.followup.send(
                "⚠️ Bot API client not initialized. Please try again shortly.",
                ephemeral=True,
            )
            return

        try:
            code, text, data = await bot.api.request(
                "POST",
                "/approvals/sync_roles",
                json={
                    "person_id": person_id,
                    "discord_user_id": str(member.id),
                    "guild_id": str(interaction.guild.id),
                },
                timeout=15,
            )
        except Exception:
            logger.exception("role sync API call failed")
            await interaction.followup.send(
                "❌ Failed to contact the server. Please try again later.",
                ephemeral=True,
            )
            return

        # Backend endpoint may not exist yet
        if code in (404, 405):
            await interaction.followup.send(
                "ℹ️ Role sync is not enabled yet. An admin will handle roles for now.",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            msg = format_api_error(code, text, data)
            await interaction.followup.send(msg, ephemeral=True)
            return

        # Expected backend response:
        # {
        #   "added_roles": ["Team"],
        #   "removed_roles": [],
        # }
        added = data.get("added_roles", []) or []
        removed = data.get("removed_roles", []) or []

        role_map = {r.name: r for r in interaction.guild.roles}

        applied_add = 0
        applied_remove = 0

        for name in added:
            role = role_map.get(name)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Campaign role sync")
                    applied_add += 1
                except Exception:
                    logger.exception("Failed to add role %s to %s", name, member.id)

        for name in removed:
            role = role_map.get(name)
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Campaign role sync")
                    applied_remove += 1
                except Exception:
                    logger.exception("Failed to remove role %s from %s", name, member.id)

        await interaction.followup.send(
            "✅ Role sync complete.\n"
            f"- Roles added: {applied_add}\n"
            f"- Roles removed: {applied_remove}",
            ephemeral=True,
        )
