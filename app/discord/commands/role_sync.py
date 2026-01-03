from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

import discord
from discord import app_commands

from .shared import api_request, format_api_error, ensure_person_by_discord
from ..config.settings import settings

if TYPE_CHECKING:
    from discord import Interaction
    from ..bot import DashboardBot

logger = logging.getLogger(__name__)


def _managed_role_names() -> List[str]:
    """
    Role names managed by the system.
    Must match the backend computation (approvals.sync_roles).
    """
    roles = [
        settings.role_team,
        settings.role_fundraising,
        settings.role_leader,
    ]
    # Optional admin role (only if you actually use it)
    admin = (getattr(settings, "role_admin", None) or "").strip()
    if admin:
        roles.append(admin)
    return [r for r in roles if r]


def _member_role_names(member: discord.Member) -> List[str]:
    return [r.name for r in (member.roles or []) if getattr(r, "name", None)]


def _find_role_by_name(guild: discord.Guild, name: str) -> discord.Role | None:
    lname = name.strip().lower()
    for r in guild.roles:
        if (r.name or "").strip().lower() == lname:
            return r
    return None


def register(bot: "DashboardBot", tree: app_commands.CommandTree) -> None:
    """
    /sync_me — self-service Discord role sync.

    Behavior:
    - Ensures a Person exists for the caller (via /people/discord/upsert).
    - Calls backend /approvals/sync_roles (authoritative source of truth).
    - Adds/removes ONLY roles managed by the system.
    - Idempotent and safe to run repeatedly.

    Permissions:
    - Any member can run this for themselves.
    """

    @tree.command(name="sync_me", description="Sync my Discord roles from dashboard access flags.")
    async def sync_me(interaction: Interaction) -> None:
        # Must be used in a guild
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        member: discord.Member = interaction.user
        guild: discord.Guild = interaction.guild

        # Acknowledge early (role ops can take a moment)
        await interaction.response.defer(ephemeral=True)

        # Ensure Person exists / linked
        person_id, _, err = await ensure_person_by_discord(bot, interaction)
        if err or not person_id:
            await interaction.followup.send(f"❌ {err or 'Unable to link your account.'}", ephemeral=True)
            return

        # Call backend for authoritative role decision
        code, text, data = await api_request(
            bot.api,
            "POST",
            "/approvals/sync_roles",
            json={
                "person_id": person_id,
                "discord_user_id": str(member.id),
                "guild_id": str(guild.id),
            },
            timeout=15,
        )

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        desired = data.get("desired_roles") or []
        added = data.get("added_roles") or []
        removed = data.get("removed_roles") or []

        # Apply changes (Discord-side)
        added_ok: List[str] = []
        removed_ok: List[str] = []
        missing: List[str] = []

        current_names = set(_member_role_names(member))

        # Add roles
        for name in added:
            if name in current_names:
                continue
            role = _find_role_by_name(guild, name)
            if not role:
                missing.append(name)
                continue
            try:
                await member.add_roles(role, reason="dashboard:/sync_me")
                added_ok.append(name)
            except Exception:
                logger.exception("Failed adding role '%s' to member %s", name, member.id)

        # Remove roles
        for name in removed:
            if name not in current_names:
                continue
            role = _find_role_by_name(guild, name)
            if not role:
                continue
            try:
                await member.remove_roles(role, reason="dashboard:/sync_me")
                removed_ok.append(name)
            except Exception:
                logger.exception("Failed removing role '%s' from member %s", name, member.id)

        # Build user-facing summary
        lines: List[str] = ["🔄 **Role sync complete**"]
        if added_ok:
            lines.append(f"➕ Added: {', '.join(added_ok)}")
        if removed_ok:
            lines.append(f"➖ Removed: {', '.join(removed_ok)}")
        if not added_ok and not removed_ok:
            lines.append("✅ Your roles were already up to date.")
        if missing:
            lines.append(
                "⚠️ These roles are expected by the system but do not exist on this server:\n"
                f"   {', '.join(missing)}"
            )

        await interaction.followup.send("\n".join(lines), ephemeral=True)
