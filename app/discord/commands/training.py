from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from discord import app_commands

from .shared import api_request, ensure_person_by_discord, format_api_error

if TYPE_CHECKING:
    import discord
    import httpx


def _clamp_limit(limit: Any, default: int = 15, lo: int = 1, hi: int = 25) -> int:
    try:
        v = int(limit if limit is not None else default)
    except Exception:
        v = default
    return max(lo, min(v, hi))


def _safe_title(x: Any) -> str:
    s = ("" if x is None else str(x)).strip()
    return s if s else "(untitled)"


def _clean_note(note: Optional[str], max_len: int = 500) -> Optional[str]:
    if note is None:
        return None
    s = str(note).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


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

        limit_i = _clamp_limit(limit, default=15, lo=1, hi=25)

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
        if not isinstance(items, list) or not items:
            await interaction.followup.send("📚 Trainings\n- (none found)", ephemeral=True)
            return

        lines: List[str] = []
        for it in items[:limit_i]:
            if not isinstance(it, dict):
                continue
            mid = it.get("id")
            title = _safe_title(it.get("title"))
            status = (str(it.get("status") or "active")).strip()
            lines.append(f"- id:{mid}  **{title}**  ({status})")

        await interaction.followup.send(
            "📚 Trainings\n" + ("\n".join(lines) if lines else "- (none found)"),
            ephemeral=True,
        )

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

        # Defensive: ensure positive int
        try:
            module_id_i = int(module_id)
        except Exception:
            await interaction.followup.send("❌ module_id must be an integer.", ephemeral=True)
            return
        if module_id_i < 1:
            await interaction.followup.send("❌ module_id must be >= 1.", ephemeral=True)
            return

        person_id, _, err = await ensure_person_by_discord(bot, interaction)
        if err or person_id is None:
            await interaction.followup.send("❌ Could not link you to a person record.\n" + (err or ""), ephemeral=True)
            return

        payload: Dict[str, Any] = {
            "person_id": person_id,
            "module_id": module_id_i,
            "note": _clean_note(note),
            "source": "discord",
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

        # Drop None values from top-level for clean API payloads
        payload = {k: v for k, v in payload.items() if v is not None}

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
            f"✅ Training marked complete.\n- module_id: {module_id_i}\n- person_id: {person_id}",
            ephemeral=True,
        )
