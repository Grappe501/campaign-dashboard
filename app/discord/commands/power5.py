from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from discord import app_commands

from .shared import api_request, ensure_person_by_discord, format_api_error

if TYPE_CHECKING:
    import discord
    import httpx

logger = logging.getLogger(__name__)


def _as_int(x: Any) -> Optional[int]:
    try:
        if isinstance(x, int):
            return x
        if isinstance(x, str) and x.strip().isdigit():
            return int(x.strip())
    except Exception:
        return None
    return None


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Power of 5 commands.

    Restores:
      - /p5_stats  (GET  /power5/teams/{team_id}/stats)
      - /p5_invite (POST /power5/teams/{team_id}/invites)
      - /p5_link   (POST /power5/teams/{team_id}/links)
      - /p5_tree   (GET  /power5/teams/{team_id}/tree)
    """

    @tree.command(name="p5_stats", description="Power of 5: show team stats (counts by depth/status).")
    @app_commands.describe(team_id="power_team_id (integer)")
    async def p5_stats(interaction: "discord.Interaction", team_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        code, text, data = await api_request(api, "GET", f"/power5/teams/{int(team_id)}/stats", timeout=15)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        by_status = data.get("by_status", {}) or {}
        by_depth = data.get("by_depth", {}) or {}

        status_lines = [f"- {k}: {v}" for k, v in sorted(by_status.items(), key=lambda kv: str(kv[0]))]

        def _depth_key(item: Any) -> int:
            try:
                return int(item[0])
            except Exception:
                return 0

        depth_lines = [f"- depth {k}: {v}" for k, v in sorted(by_depth.items(), key=_depth_key)]

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

    @tree.command(name="p5_invite", description="Power of 5: create an onboarding invite (returns token).")
    @app_commands.describe(
        team_id="power_team_id (integer)",
        invited_by_person_id="Optional: your person_id (defaults to your Discord-linked person_id)",
        channel="email|sms|discord",
        destination="email address or phone number or discord handle",
        invitee_person_id="optional existing person_id for the invitee",
    )
    async def p5_invite(
        interaction: "discord.Interaction",
        team_id: int,
        channel: str,
        destination: str,
        invited_by_person_id: Optional[int] = None,
        invitee_person_id: Optional[int] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        ch = (channel or "").strip().lower()
        if ch not in ("email", "sms", "discord"):
            await interaction.followup.send("❌ channel must be `email`, `sms`, or `discord`.", ephemeral=True)
            return

        dest = (destination or "").strip()
        if not dest:
            await interaction.followup.send("❌ destination is required.", ephemeral=True)
            return

        # Default inviter to the Discord user’s linked person_id (less typing, fewer errors)
        if invited_by_person_id is None:
            pid, _, err = await ensure_person_by_discord(bot, interaction)
            if err or pid is None:
                await interaction.followup.send(
                    "❌ I couldn't link you to a person_id in the dashboard yet.\n"
                    "Try again, or pass invited_by_person_id explicitly.",
                    ephemeral=True,
                )
                return
            invited_by_person_id = pid

        params: Dict[str, Any] = {
            "invited_by_person_id": int(invited_by_person_id),
            "channel": ch,
            "destination": dest,
        }
        if invitee_person_id is not None:
            params["invitee_person_id"] = int(invitee_person_id)

        code, text, data = await api_request(
            api,
            "POST",
            f"/power5/teams/{int(team_id)}/invites",
            params=params,
            timeout=20,
        )
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        token = data.get("token")
        expires_at = data.get("expires_at")

        if not token:
            await interaction.followup.send(
                "⚠️ Invite created, but no token was returned by the API.",
                ephemeral=True,
            )
            return

        msg = (
            f"✅ Invite created — team_id={team_id}\n"
            f"- invited_by_person_id: {invited_by_person_id}\n"
            f"- channel: {ch}\n"
            f"- destination: {dest}\n"
            f"- expires_at: {expires_at}\n\n"
            "🔑 Token:\n"
            f"`{token}`\n\n"
            "Tip: share this token with the invitee to use during onboarding/claim."
        )
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="p5_link",
        description="Power of 5: link recruiter -> recruit inside a team (creates/updates a link).",
    )
    @app_commands.describe(
        team_id="power_team_id",
        parent_person_id="recruiter person_id",
        child_person_id="recruit person_id",
        status="invited|onboarded|active|churned (default invited)",
    )
    async def p5_link(
        interaction: "discord.Interaction",
        team_id: int,
        parent_person_id: int,
        child_person_id: int,
        status: str = "invited",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        st = (status or "").strip().lower()
        if st not in ("invited", "onboarded", "active", "churned"):
            await interaction.followup.send(
                "❌ status must be `invited`, `onboarded`, `active`, or `churned`.",
                ephemeral=True,
            )
            return

        payload: Dict[str, Any] = {
            "power_team_id": int(team_id),
            "parent_person_id": int(parent_person_id),
            "child_person_id": int(child_person_id),
            "status": st,
        }

        code, text, data = await api_request(
            api,
            "POST",
            f"/power5/teams/{int(team_id)}/links",
            json=payload,
            timeout=20,
        )
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
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

    @tree.command(name="p5_tree", description="Power of 5: show simple tree adjacency (compact).")
    @app_commands.describe(team_id="power_team_id")
    async def p5_tree(interaction: "discord.Interaction", team_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        code, text, data = await api_request(api, "GET", f"/power5/teams/{int(team_id)}/tree", timeout=20)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        children = data.get("children", {}) or {}
        leader_id = data.get("leader_person_id")

        lines: List[str] = [f"Leader: {leader_id}"]
        shown = 0

        for parent, kids in (children.items() if isinstance(children, dict) else []):
            if shown >= 30:
                lines.append("…(truncated)")
                break

            kid_parts: List[str] = []
            if isinstance(kids, list):
                for k in kids:
                    if not isinstance(k, dict):
                        continue
                    cid = _as_int(k.get("child_person_id"))
                    depth = k.get("depth")
                    st = k.get("status")
                    if cid is None:
                        continue
                    kid_parts.append(f"{cid} (d{depth},{st})")
            else:
                kid_parts.append(str(kids))

            lines.append(f"{parent} -> " + (", ".join(kid_parts) if kid_parts else "(none)"))
            shown += 1

        await interaction.followup.send("🌳 Power of 5 Tree\n" + "\n".join(lines), ephemeral=True)
