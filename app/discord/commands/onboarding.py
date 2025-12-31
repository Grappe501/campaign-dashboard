from __future__ import annotations

from typing import Any, List, Optional

import discord
from discord import app_commands

from ...config.settings import settings
from .shared import (
    api_request,
    ensure_person_by_discord,
    format_api_error,
    next_step_for_stage,
)


def _onboarding_message_fallback() -> str:
    lines: List[str] = [
        "👋 **Welcome to the campaign volunteer hub!**",
        "",
        "**Do this in order (takes ~3 minutes):**",
        f"1) Pick **one small action** from **#{settings.first_actions_channel_name}** (call/text/share/sign up a friend).",
        "2) Log it with `/log` (example: `/log action_type:call quantity:10`).",
        f"3) Post a {settings.wins_trigger_emoji} in **#{settings.wins_channel_name}** so we can celebrate you.",
        "",
        "**Need to get placed on a team?** Use `/request_team_access request_type:team`.",
        "**Need fundraising lane access?** Use `/request_team_access request_type:fundraising`.",
    ]
    if settings.volunteer_form_url:
        lines.append("")
        lines.append(f"📝 Volunteer form: {settings.volunteer_form_url}")
    if settings.onboarding_url:
        lines.append(f"🌐 Onboarding page: {settings.onboarding_url}")
    if settings.discord_help_url:
        lines.append(f"❓ Discord help: {settings.discord_help_url}")
    return "\n".join(lines)


def _format_next_steps(next_steps: Any) -> str:
    if not next_steps:
        return ""
    if isinstance(next_steps, list):
        bullets: List[str] = []
        for i, s in enumerate(next_steps[:8], start=1):
            try:
                item = str(s).strip()
            except Exception:
                continue
            if item:
                bullets.append(f"{i}) {item}")
        return "\n".join(bullets)
    try:
        return str(next_steps)
    except Exception:
        return ""


def register(bot: discord.Client, tree: app_commands.CommandTree) -> None:
    """
    Onboarding commands.

    Restores:
      - /start  (onboarding + next steps)
      - /whoami (identity details used for linking/logging)

    Contract:
      - POST /people/discord/upsert
      - POST /people/onboard
    """

    @tree.command(name="start", description="Start here: onboarding + next steps.")
    async def start(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        api = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send(_onboarding_message_fallback(), ephemeral=True)
            return

        person_id, person_data, err = await ensure_person_by_discord(bot, interaction)
        if err and person_id is None:
            await interaction.followup.send(
                _onboarding_message_fallback()
                + "\n\n⚠️ Note: I couldn't link you to the dashboard API right now, but you can still follow the steps above.",
                ephemeral=True,
            )
            return

        onboard_payload = {"person_id": person_id, "discord_user_id": str(interaction.user.id)}
        code, text, data = await api_request(api, "POST", "/people/onboard", json=onboard_payload, timeout=20)

        if code != 200 or not isinstance(data, dict):
            stage = None
            if isinstance(person_data, dict):
                stage = person_data.get("stage")

            msg = _onboarding_message_fallback()
            msg += "\n\n---\n"
            if person_id:
                msg += f"🆔 Linked person_id: **{person_id}**\n"
            msg += "\nNext step:\n" + next_step_for_stage(stage if isinstance(stage, str) else None)
            msg += "\n\n⚠️ Note: I couldn't complete onboarding in the API yet.\n"
            msg += format_api_error(code, text, data)
            await interaction.followup.send(msg, ephemeral=True)
            return

        p = data.get("person") or {}
        next_steps = data.get("next_steps") or []

        stage_val: Optional[str] = None
        if isinstance(p, dict):
            try:
                stage_val = p.get("stage")
            except Exception:
                stage_val = None

        msg_lines: List[str] = [
            "✅ You’re onboarded.",
            f"🆔 person_id: **{(p.get('id') if isinstance(p, dict) else None) or person_id}**",
        ]
        if stage_val:
            msg_lines.append(f"📍 Stage: **{str(stage_val).upper()}**")

        msg_lines.append("")
        msg_lines.append("Next steps:")
        formatted = _format_next_steps(next_steps)
        msg_lines.append(formatted if formatted else next_step_for_stage(stage_val))

        extra: List[str] = []
        if settings.volunteer_form_url:
            extra.append(f"📝 Volunteer form: {settings.volunteer_form_url}")
        if settings.onboarding_url:
            extra.append(f"🌐 Onboarding page: {settings.onboarding_url}")
        if settings.discord_help_url:
            extra.append(f"❓ Discord help: {settings.discord_help_url}")
        if extra:
            msg_lines.append("")
            msg_lines.extend(extra)

        await interaction.followup.send("\n".join(msg_lines), ephemeral=True)

    @tree.command(name="whoami", description="Show your Discord identity details used for linking/logging.")
    async def whoami(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "🪪 Identity\n"
            f"- discord_user_id: {interaction.user.id}\n"
            f"- username: {interaction.user}\n"
            f"- display_name: {interaction.user.display_name}\n"
            f"- guild_id: {interaction.guild_id}\n"
            f"- channel_id: {interaction.channel_id}",
            ephemeral=True,
        )
