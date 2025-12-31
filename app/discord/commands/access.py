from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

import discord
from discord import app_commands

from ...config.settings import settings
from .shared import api_request, format_api_error

if TYPE_CHECKING:
    import httpx


def _normalize_name(s: str) -> str:
    return (s or "").strip().lower()


def _find_role(guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
    """
    Find a role by name, case-insensitive (exact match first, then normalized scan).
    """
    if not role_name:
        return None

    # Fast path: exact match
    role = discord.utils.get(guild.roles, name=role_name)
    if role is not None:
        return role

    target = _normalize_name(role_name)
    for r in guild.roles:
        if _normalize_name(r.name) == target:
            return r
    return None


def _bot_can_manage_role(me: discord.Member, role: discord.Role) -> bool:
    """
    Discord rule: a bot can only manage roles that are below its top role,
    and it must have Manage Roles permission.
    """
    if not me.guild_permissions.manage_roles:
        return False
    try:
        return me.top_role > role
    except Exception:
        return False


async def _resolve_bot_member(
    guild: discord.Guild,
    bot_user: Optional[discord.abc.User],
) -> Optional[discord.Member]:
    """
    Resolve the bot as a guild Member reliably.
    Uses cache first, then fetch as fallback.

    Note: fetch_member may require members intent depending on server/app config.
    """
    if bot_user is None:
        return None

    cached = guild.get_member(bot_user.id)
    if isinstance(cached, discord.Member):
        return cached

    try:
        fetched = await guild.fetch_member(bot_user.id)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        return None

    return None


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Access / Role Sync commands.

    Restores:
      - /sync_me  Sync your Discord roles based on dashboard access flags.

    Backend expectation:
      GET /access/discord_user?discord_user_id=<id>
      Returns:
        { "access": { "team": bool, "fundraising": bool, "leader": bool } }
    """

    @tree.command(name="sync_me", description="Sync your Discord roles based on dashboard access (Phase 4).")
    async def sync_me(interaction: "discord.Interaction") -> None:
        await interaction.response.defer(ephemeral=True)

        if not settings.enable_role_sync:
            await interaction.followup.send("Role sync is disabled by config.", ephemeral=True)
            return

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("❌ This command must be used inside a server (guild).", ephemeral=True)
            return

        # Guard: if no roles are configured, tell the admin explicitly.
        if not (settings.role_team or settings.role_fundraising or settings.role_leader):
            await interaction.followup.send(
                "⚠️ No role names are configured for role sync.\n"
                "- Set ROLE_TEAM / ROLE_FUNDRAISING / ROLE_LEADER (or your settings equivalents) and try again.",
                ephemeral=True,
            )
            return

        # Resolve bot member reliably (avoid guild.me quirks / deprecations)
        bot_user = getattr(bot, "user", None)
        me = await _resolve_bot_member(guild, bot_user)
        if me is None:
            await interaction.followup.send(
                "❌ Could not resolve bot member in this guild.\n"
                "Tip: If you disabled the Members intent, enable it for role sync, or ensure the bot can fetch members.",
                ephemeral=True,
            )
            return

        if not me.guild_permissions.manage_roles:
            await interaction.followup.send("❌ Bot lacks **Manage Roles** permission (cannot sync roles).", ephemeral=True)
            return

        # Fetch access flags from backend
        code, text, data = await api_request(
            api,
            "GET",
            "/access/discord_user",
            params={"discord_user_id": str(interaction.user.id)},
            timeout=15,
        )

        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Role sync API isn't available yet. (Endpoint pending: `/access/discord_user`)",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        access = data.get("access")
        if not isinstance(access, dict):
            await interaction.followup.send(
                "⚠️ Role sync: response received but missing `access` object.",
                ephemeral=True,
            )
            return

        want_team = bool(access.get("team"))
        want_fundraising = bool(access.get("fundraising"))
        want_leader = bool(access.get("leader"))

        changes: List[str] = []
        issues: List[str] = []

        async def _ensure_role(role_name: str, should_have: bool) -> None:
            if not role_name:
                return

            role = _find_role(guild, role_name)
            if role is None:
                issues.append(f"Role not found: '{role_name}'")
                return

            if not _bot_can_manage_role(me, role):
                issues.append(f"Bot cannot manage role '{role.name}' (bot top role must be ABOVE it).")

            member: discord.Member = interaction.user
            has = role in member.roles

            if should_have and not has:
                try:
                    await member.add_roles(role, reason="Dashboard role sync")
                    changes.append(f"+ {role.name}")
                except discord.Forbidden:
                    issues.append(f"Add '{role.name}': forbidden (permissions/hierarchy).")
                except Exception as e:
                    issues.append(f"Add '{role.name}': {e}")

            if (not should_have) and has:
                try:
                    await member.remove_roles(role, reason="Dashboard role sync")
                    changes.append(f"- {role.name}")
                except discord.Forbidden:
                    issues.append(f"Remove '{role.name}': forbidden (permissions/hierarchy).")
                except Exception as e:
                    issues.append(f"Remove '{role.name}': {e}")

        await _ensure_role(settings.role_team, want_team)
        await _ensure_role(settings.role_fundraising, want_fundraising)
        await _ensure_role(settings.role_leader, want_leader)

        msg = (
            "🔁 Role sync complete.\n"
            f"- access: team={want_team}, fundraising={want_fundraising}, leader={want_leader}\n"
            f"- changes: {', '.join(changes) if changes else '(none)'}\n"
        )
        if issues:
            msg += "⚠️ issues:\n" + "\n".join([f"- {e}" for e in issues])

        await interaction.followup.send(msg, ephemeral=True)
