from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set, Tuple

import discord
import httpx
from discord import app_commands

from ...config.settings import settings
from .shared import (
    api_request,
    approval_type_from_user,
    ensure_person_by_discord,
    format_api_error,
    role_name_for_request_type,
    split_csv,
)

if TYPE_CHECKING:
    from discord import Interaction
    from discord import app_commands as app_commands_typing

logger = logging.getLogger(__name__)

# -----------------------------
# Role guards (Discord-side)
# -----------------------------


def _normalize_name(s: str) -> str:
    return (s or "").strip().lower()


def _parse_role_specs(role_specs: List[str]) -> Tuple[Set[int], Set[str]]:
    """
    Parse a list of role specs into (role_ids, role_names_normalized).

    Specs can be:
      - numeric role IDs: "1234567890"
      - role names: "Admin", "Campaign Admin"
    """
    role_ids: Set[int] = set()
    role_names: Set[str] = set()

    for spec in role_specs or []:
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

    return role_ids, role_names


# Evaluate configured role specs at import time (settings are env-backed and immutable per-process)
ADMIN_ROLE_SPECS: List[str] = split_csv(settings.admin_roles_raw)
LEAD_ROLE_SPECS: List[str] = split_csv(settings.lead_roles_raw)  # reserved for future lead gating

_ADMIN_ROLE_IDS, _ADMIN_ROLE_NAMES = _parse_role_specs(ADMIN_ROLE_SPECS)
_LEAD_ROLE_IDS, _LEAD_ROLE_NAMES = _parse_role_specs(LEAD_ROLE_SPECS)  # reserved (unused currently)


def _member_has_any_role(member: discord.abc.User, role_ids: Set[int], role_names: Set[str]) -> bool:
    """
    Check whether a member has ANY of the specified role ids or names.
    Fail-closed: returns False if user isn't a guild Member or if no specs provided.
    """
    if not role_ids and not role_names:
        return False
    if not isinstance(member, discord.Member):
        return False

    for r in getattr(member, "roles", []) or []:
        try:
            if role_ids and int(getattr(r, "id", 0)) in role_ids:
                return True
            if role_names and _normalize_name(getattr(r, "name", "")) in role_names:
                return True
        except Exception:
            continue

    return False


def _is_admin(interaction: "Interaction") -> bool:
    """
    Admin guard (fail-closed):

    - Must be invoked in a guild by a Member.
    - If DASHBOARD_ADMIN_ROLES is configured: role-based ONLY.
    - Else fallback: Manage Guild or Administrator permission.
    """
    guild = interaction.guild
    u = interaction.user

    # Fail-closed: no guild => no admin
    if guild is None:
        return False
    if not isinstance(u, discord.Member):
        return False

    # If admin roles are configured, we ONLY accept those (deterministic, no surprises).
    if _ADMIN_ROLE_IDS or _ADMIN_ROLE_NAMES:
        return _member_has_any_role(u, _ADMIN_ROLE_IDS, _ADMIN_ROLE_NAMES)

    # Fallback (only when no roles configured)
    perms = u.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def _guard(check_fn: Callable[["Interaction"], bool], fail_msg: str):
    """
    app_commands.check wrapper that also sends an ephemeral failure message.
    """

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


# -----------------------------
# Role sync on approve (best-effort, safe)
# -----------------------------


def _find_role(guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
    """
    Find a role by name, case-insensitive (exact match first, then normalized scan).
    """
    if not role_name:
        return None

    role = discord.utils.get(guild.roles, name=role_name)
    if role is not None:
        return role

    target = _normalize_name(role_name)
    for r in guild.roles:
        if _normalize_name(r.name) == target:
            return r
    return None


def _is_disallowed_role(guild: discord.Guild, role: discord.Role) -> bool:
    """
    Prevent acting on roles that Discord either forbids or that are risky.
    """
    # @everyone role id == guild id
    if role.id == guild.id:
        return True
    if getattr(role, "managed", False):
        return True
    return False


def _bot_can_manage_role(me: discord.Member, role: discord.Role) -> bool:
    """
    Discord rule: a bot can only manage roles below its top role,
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
    """
    if bot_user is None:
        return None

    me = guild.get_member(bot_user.id)
    if isinstance(me, discord.Member):
        return me

    try:
        fetched = await guild.fetch_member(bot_user.id)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        return None

    return None


async def _apply_role_for_approval(
    *,
    interaction: "Interaction",
    approval_request_type: str,
    target_discord_user_id: str,
) -> Optional[str]:
    """
    Best-effort: apply the configured Discord role matching the request type to the target user.

    Hardening:
      - Fail-closed on missing guild/member/bot perms
      - Disallow @everyone/managed roles
      - Do not attempt changes if not manageable
      - Return role name if applied or already present
      - Raise RuntimeError with a user-safe message if a specific constraint prevents syncing
    """
    guild = interaction.guild
    if guild is None:
        return None

    role_name = role_name_for_request_type(approval_request_type)
    if not role_name:
        return None

    if not target_discord_user_id or not target_discord_user_id.isdigit():
        return None

    bot_user = getattr(interaction.client, "user", None)
    me = await _resolve_bot_member(guild, bot_user)
    if me is None:
        raise RuntimeError("Could not resolve bot member in guild.")
    if not me.guild_permissions.manage_roles:
        raise RuntimeError("Bot lacks Manage Roles permission.")

    role = _find_role(guild, role_name)
    if role is None:
        raise RuntimeError(f"Role '{role_name}' not found in this server.")

    if _is_disallowed_role(guild, role):
        raise RuntimeError(f"Role '{role.name}' cannot be managed (@everyone/managed role).")

    if not _bot_can_manage_role(me, role):
        raise RuntimeError(f"Bot cannot manage role '{role.name}' (check role hierarchy).")

    member = guild.get_member(int(target_discord_user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(target_discord_user_id))
        except Exception:
            member = None
    if member is None:
        raise RuntimeError("Target member not found in guild.")

    if role in member.roles:
        return role.name

    try:
        await member.add_roles(role, reason="Dashboard approval granted")
        return role.name
    except discord.Forbidden:
        raise RuntimeError("Discord denied role change (permissions/hierarchy).")
    except Exception:
        raise RuntimeError("Failed to apply role due to an unexpected Discord error.")


# -----------------------------
# UI Components (Approvals)
# -----------------------------


class _ReviewReasonModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        decision: str,
        approval_id: int,
        request_type: Optional[str] = None,
        discord_user_id: Optional[str] = None,
    ) -> None:
        super().__init__(title=title, timeout=300)
        self.decision = decision
        self.approval_id = approval_id
        self.request_type = request_type
        self.discord_user_id = discord_user_id

        self.reason = discord.ui.TextInput(
            label="Reason (optional)",
            placeholder="Short reason (optional)…",
            required=False,
            max_length=300,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: "Interaction") -> None:
        await interaction.response.defer(ephemeral=True)

        # Guard again (buttons can be clicked later; do not trust view creation time).
        if not _is_admin(interaction):
            await interaction.followup.send("❌ Admin only.", ephemeral=True)
            return

        reason = (str(self.reason.value).strip() if self.reason.value is not None else "").strip() or None

        api: Optional[httpx.AsyncClient] = getattr(interaction.client, "api", None)  # type: ignore[attr-defined]
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        reviewer_person_id, _, err = await ensure_person_by_discord(api, interaction)
        if err or reviewer_person_id is None:
            await interaction.followup.send(
                "❌ I couldn't link you to a reviewer person_id in the dashboard.\n" + (err or ""),
                ephemeral=True,
            )
            return

        payload = {"reviewer_person_id": reviewer_person_id, "decision": self.decision, "reason": reason}

        code, text, data = await api_request(
            api,
            "POST",
            f"/approvals/{self.approval_id}/review",
            json=payload,
            timeout=25,
        )
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        stage_changed_to = data.get("stage_changed_to")
        approval = data.get("approval") or {}

        applied_role: Optional[str] = None
        role_error: Optional[str] = None
        if self.decision == "approve" and settings.enable_role_sync:
            try:
                applied_role = await _apply_role_for_approval(
                    interaction=interaction,
                    approval_request_type=str(approval.get("request_type") or self.request_type or ""),
                    target_discord_user_id=str(approval.get("discord_user_id") or self.discord_user_id or ""),
                )
            except Exception as e:
                role_error = str(e)

        msg = (
            "✅ Review saved.\n"
            f"- approval_id: {approval.get('id', self.approval_id)}\n"
            f"- status: {approval.get('status')}\n"
            f"- request_type: {approval.get('request_type')}\n"
        )
        if stage_changed_to:
            msg += f"- stage_changed_to: {stage_changed_to}\n"
        if applied_role:
            msg += f"- discord_role_applied: {applied_role}\n"
        if role_error:
            msg += f"⚠️ Role sync issue: {role_error}\n"

        await interaction.followup.send(msg, ephemeral=True)


class ApprovalsReviewView(discord.ui.View):
    """
    Compact approvals UX: shows Approve/Deny buttons for a single approval_id.
    Keeps legacy /approve command intact.
    """

    def __init__(self, approval_id: int, request_type: Optional[str] = None, discord_user_id: Optional[str] = None):
        super().__init__(timeout=900)
        self.approval_id = approval_id
        self.request_type = request_type
        self.discord_user_id = discord_user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve_btn(self, interaction: "Interaction", button: discord.ui.Button) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_modal(
            _ReviewReasonModal(
                title="Approve — optional note",
                decision="approve",
                approval_id=self.approval_id,
                request_type=self.request_type,
                discord_user_id=self.discord_user_id,
            )
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny_btn(self, interaction: "Interaction", button: discord.ui.Button) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_modal(
            _ReviewReasonModal(
                title="Deny — optional reason",
                decision="deny",
                approval_id=self.approval_id,
                request_type=self.request_type,
                discord_user_id=self.discord_user_id,
            )
        )


# -----------------------------
# Public register()
# -----------------------------


def register(bot: "discord.Client", tree: "app_commands_typing.CommandTree") -> None:
    """
    Approvals commands.

    Restores:
      - /request_team_access
      - /approvals_pending (admin list + buttons)
      - /approve (legacy)
    """

    @tree.command(
        name="request_team_access",
        description="Request human-approved access (TEAM, FUNDRAISING, or LEADER).",
    )
    @app_commands.describe(
        request_type="team|fundraising|leader",
        notes="Optional: what access you need + why (short).",
    )
    async def request_team_access(
        interaction: discord.Interaction,
        request_type: str = "team",
        notes: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional[httpx.AsyncClient] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        api_rt = approval_type_from_user(request_type)
        if not api_rt:
            await interaction.followup.send(
                "❌ request_type must be `team`, `fundraising`, or `leader` (or *_access).",
                ephemeral=True,
            )
            return

        payload: Dict[str, Any] = {
            "discord_user_id": str(interaction.user.id),
            "name": interaction.user.display_name,
            "request_type": api_rt,
            "notes": notes,
        }

        code, text, data = await api_request(api, "POST", "/approvals/request", json=payload, timeout=20)
        if code != 200 or not data:
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        await interaction.followup.send(
            "✅ Request submitted.\n"
            f"- approval_id: {data.get('id')}\n"
            f"- request_type: {data.get('request_type')}\n"
            f"- status: {data.get('status')}\n\n"
            "A campaign admin will review it shortly.\n"
            f"Tip: you can keep logging wins while you wait in **#{settings.wins_channel_name}**.",
            ephemeral=True,
        )

    @tree.command(name="approvals_pending", description="Admin: list pending approval requests (with buttons).")
    @app_commands.describe(limit="Max items (default 10)", request_type="Optional: team|fundraising|leader")
    @_guard(_is_admin, "❌ Admin only. You need a configured admin role or Manage Server permission.")
    async def approvals_pending(
        interaction: discord.Interaction,
        limit: int = 10,
        request_type: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional[httpx.AsyncClient] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        params: Dict[str, Any] = {"limit": max(1, min(int(limit or 10), 20))}
        if request_type:
            api_rt = approval_type_from_user(request_type)
            if not api_rt:
                await interaction.followup.send(
                    "❌ request_type must be `team`, `fundraising`, or `leader` (or *_access).",
                    ephemeral=True,
                )
                return
            params["request_type"] = api_rt

        code, text, data = await api_request(api, "GET", "/approvals/pending", params=params, timeout=20)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        items = data.get("items") or []
        if not items:
            await interaction.followup.send("✅ No pending approvals right now.", ephemeral=True)
            return

        await interaction.followup.send(
            f"🗳️ Pending approvals: {len(items)} (showing up to {params['limit']})",
            ephemeral=True,
        )

        for it in items[: params["limit"]]:
            try:
                aid = int(it.get("id"))
            except Exception:
                continue

            rt = str(it.get("request_type") or "")
            duid = str(it.get("discord_user_id") or "")
            name = str(it.get("name") or "")
            status = str(it.get("status") or "")

            header = (
                f"**Approval #{aid}**\n"
                f"- type: `{rt}`\n"
                f"- status: `{status}`\n"
                f"- user: `{duid}`\n"
                f"- name: {name}\n"
                f"- discord_role_on_approve: `{role_name_for_request_type(rt) or '(none)'}`"
            )
            view = ApprovalsReviewView(approval_id=aid, request_type=rt, discord_user_id=duid)
            await interaction.followup.send(header, view=view, ephemeral=True)

    @tree.command(name="approve", description="Admin: approve or deny an approval request (legacy command).")
    @app_commands.describe(
        approval_id="Approval request id",
        decision="approve|deny",
        reason="Optional: short reason (stored on approval request)",
    )
    @_guard(_is_admin, "❌ Admin only. You need a configured admin role or Manage Server permission.")
    async def approve(
        interaction: discord.Interaction,
        approval_id: int,
        decision: str,
        reason: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional[httpx.AsyncClient] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        d = (decision or "").strip().lower()
        if d not in ("approve", "deny"):
            await interaction.followup.send("❌ decision must be `approve` or `deny`.", ephemeral=True)
            return

        reviewer_person_id, _, err = await ensure_person_by_discord(api, interaction)
        if err or reviewer_person_id is None:
            await interaction.followup.send(
                "❌ I couldn't link you to a reviewer person_id in the dashboard.\n" + (err or ""),
                ephemeral=True,
            )
            return

        payload = {"reviewer_person_id": reviewer_person_id, "decision": d, "reason": reason}

        code, text, data = await api_request(api, "POST", f"/approvals/{approval_id}/review", json=payload, timeout=25)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        stage_changed_to = data.get("stage_changed_to")
        approval = data.get("approval") or {}

        applied_role: Optional[str] = None
        role_error: Optional[str] = None
        if d == "approve" and settings.enable_role_sync:
            try:
                applied_role = await _apply_role_for_approval(
                    interaction=interaction,
                    approval_request_type=str(approval.get("request_type") or ""),
                    target_discord_user_id=str(approval.get("discord_user_id") or ""),
                )
            except Exception as e:
                role_error = str(e)

        msg = (
            "✅ Review saved.\n"
            f"- approval_id: {approval.get('id', approval_id)}\n"
            f"- status: {approval.get('status')}\n"
            f"- request_type: {approval.get('request_type')}\n"
        )
        if stage_changed_to:
            msg += f"- stage_changed_to: {stage_changed_to}\n"
        if applied_role:
            msg += f"- discord_role_applied: {applied_role}\n"
        if role_error:
            msg += f"⚠️ Role sync issue: {role_error}\n"

        await interaction.followup.send(msg, ephemeral=True)
