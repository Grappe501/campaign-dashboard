from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from discord import app_commands

from .shared import api_request, ensure_person_by_discord, format_api_error

if TYPE_CHECKING:
    import discord
    import httpx

logger = logging.getLogger(__name__)


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Training / SOP System commands.

    Provides:
      - /trainings         (GET  /training/modules)
      - /training_complete (POST /training/complete)
    """

    @tree.command(name="trainings", description="List training modules (Phase 4).")
    @app_commands.describe(limit="Max items (default 15)")
    async def trainings(interaction: "discord.Interaction", limit: int = 15) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        # Clamp for safety
        try:
            limit_i = int(limit or 15)
        except Exception:
            limit_i = 15
        limit_i = max(1, min(limit_i, 25))

        code, text, data = await api_request(
            api,
            "GET",
            "/training/modules",
            params={"limit": limit_i},
            timeout=20,
        )

        # Graceful if endpoint not shipped yet
        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Training API not available yet (endpoint pending: `/training/modules`).",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        items = data.get("items") or []
        if not items:
            await interaction.followup.send("No trainings found.", ephemeral=True)
            return

        lines: List[str] = []
        for it in items[:limit_i]:
            try:
                lines.append(f"- id:{it.get('id')}  **{it.get('title')}**  ({it.get('status', 'active')})")
            except Exception:
                continue

        await interaction.followup.send("📚 Trainings\n" + "\n".join(lines), ephemeral=True)

    @tree.command(name="training_complete", description="Mark a training module complete for you (Phase 4).")
    @app_commands.describe(module_id="Training module id", note="Optional note (link, proof, etc.)")
    async def training_complete(
        interaction: "discord.Interaction",
        module_id: int,
        note: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        person_id, _, err = await ensure_person_by_discord(bot, interaction)
        if err or person_id is None:
            await interaction.followup.send("❌ Could not link you to a person record.\n" + (err or ""), ephemeral=True)
            return

        payload: Dict[str, Any] = {
            "person_id": person_id,
            "module_id": module_id,
            "note": note,
            "source": "discord",
            # Optional: include discord metadata for backend audit logging
            "meta": {
                "discord": {
                    "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
                    "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
                    "user_id": str(interaction.user.id) if interaction.user else None,
                    "username": str(interaction.user) if interaction.user else None,
                    "interaction_id": str(interaction.id),
                }
            },
        }

        code, text, data = await api_request(api, "POST", "/training/complete", json=payload, timeout=20)

        # Graceful if endpoint not shipped yet
        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Training completion API not available yet (endpoint pending: `/training/complete`).",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Training marked complete.\n- module_id: {module_id}\n- person_id: {person_id}",
            ephemeral=True,
        )
