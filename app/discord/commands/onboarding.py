from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from ...config.settings import settings
from .shared import api_request, ensure_person_by_discord, format_api_error, next_step_for_stage

# Accept ZIP5 or ZIP+4. We send the cleaned string to the API; the backend normalizes/stores safely.
_ZIP_RE = re.compile(r"^\s*(\d{5})(?:-(\d{4}))?\s*$")


def _safe_channel(name: str, fallback: str) -> str:
    s = (name or "").strip()
    return s if s else fallback


def _safe_emoji(value: str, fallback: str = "✅") -> str:
    s = (value or "").strip()
    return s if s else fallback


def _clean_full_name(raw: Optional[str], max_len: int = 120) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _clean_email(raw: Optional[str], max_len: int = 200) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[:max_len]
    # Light-touch sanity only (don’t block valid edge cases). Backend may validate.
    return s


def _clean_phone(raw: Optional[str], max_len: int = 40) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _clean_zip(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (zip_value, error_message).

    Accepts:
      - 12345
      - 12345-6789

    Returns:
      - "12345" or "12345-6789" (normalized)
    """
    if raw is None:
        return None, "ZIP code is required."
    s = str(raw).strip()
    if not s:
        return None, "ZIP code is required."
    m = _ZIP_RE.match(s)
    if not m:
        return None, "Please enter a valid ZIP code (e.g., 72201 or 72201-1234)."
    zip5 = m.group(1)
    plus4 = m.group(2)
    return f"{zip5}-{plus4}" if plus4 else zip5, None


def _onboarding_message_fallback() -> str:
    """
    Always safe to render (no API dependency). Used as fallback when API is unavailable.
    """
    wins_channel = _safe_channel(settings.wins_channel_name, "wins-and-updates")
    first_actions_channel = _safe_channel(settings.first_actions_channel_name, "first-actions")
    emoji = _safe_emoji(settings.wins_trigger_emoji, "✅")

    lines: List[str] = [
        "👋 **Welcome to the campaign volunteer hub!**",
        "",
        "**Do this in order (takes ~3 minutes):**",
        f"1) Pick **one small action** from **#{first_actions_channel}** (call/text/share/sign up a friend).",
        "2) Log it with `/log` (example: `/log action_type:call quantity:10`).",
        f"3) Post a {emoji} in **#{wins_channel}** so we can celebrate you.",
        "",
        "**Next: complete registration (Name + ZIP)** so we can place you geographically.",
        "Use `/register`.",
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
    """
    Accepts list-like or string-like. Returns a short formatted section.
    """
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
        return str(next_steps).strip()
    except Exception:
        return ""


def _discord_context(interaction: discord.Interaction) -> Dict[str, str]:
    """
    Adds audit/sync context fields, only when available.
    """
    ctx: Dict[str, str] = {"username": str(interaction.user)}
    if interaction.guild_id:
        ctx["guild_id"] = str(interaction.guild_id)
    if interaction.channel_id:
        ctx["channel_id"] = str(interaction.channel_id)
    return ctx


class RegistrationModal(discord.ui.Modal, title="Volunteer registration (Name + ZIP)"):
    full_name = discord.ui.TextInput(
        label="Full name",
        required=True,
        max_length=120,
        placeholder="Jane Doe",
    )
    zip_code = discord.ui.TextInput(
        label="ZIP code",
        required=True,
        max_length=10,
        placeholder="72201 or 72201-1234",
    )
    email = discord.ui.TextInput(
        label="Email (optional)",
        required=False,
        max_length=200,
        placeholder="jane@example.com",
    )
    phone = discord.ui.TextInput(
        label="Phone (optional)",
        required=False,
        max_length=40,
        placeholder="(501) 555-1234",
    )

    def __init__(self, *, bot: discord.Client) -> None:
        super().__init__()
        self._bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        api = getattr(self._bot, "api", None)
        if api is None:
            await interaction.followup.send(
                "⚠️ The dashboard API client isn’t initialized, so I can’t save your registration right now.\n\n"
                + _onboarding_message_fallback(),
                ephemeral=True,
            )
            return

        name = _clean_full_name(str(self.full_name.value) if self.full_name.value else None)
        zip_value, zerr = _clean_zip(str(self.zip_code.value) if self.zip_code.value else None)

        if not name:
            await interaction.followup.send("❌ Full name is required.", ephemeral=True)
            return
        if zerr or not zip_value:
            await interaction.followup.send(f"❌ {zerr or 'ZIP code is required.'}", ephemeral=True)
            return

        payload: Dict[str, Any] = {
            # API contract for POST /people/discord/register
            "discord_user_id": str(interaction.user.id),
            "name": name,
            "zip_code": zip_value,
            "email": _clean_email(str(self.email.value) if self.email.value else None),
            "phone": _clean_phone(str(self.phone.value) if self.phone.value else None),
            **_discord_context(interaction),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        # Call registration endpoint directly (it upserts by discord_user_id).
        code, text, data = await api_request(api, "POST", "/people/discord/register", json=payload, timeout=20)

        if code in (404, 405):
            msg = (
                "⚠️ Registration saving isn’t available yet (missing API endpoint: `POST /people/discord/register`).\n\n"
                "For now, you’re still in — but we can’t place you by ZIP automatically yet.\n"
                "Please share your ZIP with a lead/admin or fill the volunteer form below if available."
            )
            if settings.volunteer_form_url:
                msg += f"\n\n📝 Volunteer form: {settings.volunteer_form_url}"
            await interaction.followup.send(msg, ephemeral=True)
            return

        if code == 409:
            # Backend will now return 409 for unique constraint conflicts instead of a 500.
            await interaction.followup.send(
                "⚠️ I hit a save conflict while registering you (this can happen if two saves happen at the same time).\n"
                "Please run `/register` again in a few seconds.\n\n"
                + format_api_error(code, text, data),
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(
                "❌ I couldn't save your registration yet.\n" + format_api_error(code, text, data),
                ephemeral=True,
            )
            return

        # Expected response: { person: {...}, next_steps: [...] }
        person_obj = data.get("person") if isinstance(data.get("person"), dict) else None
        next_steps = data.get("next_steps") if isinstance(data.get("next_steps"), list) else None

        saved_zip = zip_value
        saved_name = name
        saved_person_id: Optional[int] = None

        if isinstance(person_obj, dict):
            try:
                saved_zip = str(person_obj.get("zip_code") or saved_zip)
            except Exception:
                pass
            try:
                saved_name = str(person_obj.get("name") or saved_name)
            except Exception:
                pass
            try:
                pid = person_obj.get("id")
                saved_person_id = pid if isinstance(pid, int) else None
            except Exception:
                pass

        lines: List[str] = [
            "✅ Registration saved.",
        ]
        if saved_person_id is not None:
            lines.append(f"🆔 person_id: **{saved_person_id}**")
        lines.extend(
            [
                f"👤 Name: **{saved_name}**",
                f"📍 ZIP: **{saved_zip}**",
                "",
                "Next steps:",
            ]
        )

        formatted = _format_next_steps(next_steps) if next_steps else ""
        if formatted:
            lines.append(formatted)
        else:
            lines.append("1) Do one small action today and log it as a win.")
            lines.append("2) If you need lane access, request TEAM access (human-approved).")

        lines.append("")
        lines.append("Need deeper access? Use `/request_team_access request_type:team`.")

        await interaction.followup.send("\n".join(lines), ephemeral=True)


class RegistrationLaunchView(discord.ui.View):
    """
    Attached to /start output so the volunteer can finish registration with one click.
    """

    def __init__(self, *, bot: discord.Client) -> None:
        super().__init__(timeout=180)
        self._bot = bot

    @discord.ui.button(label="Complete Registration (Name + ZIP)", style=discord.ButtonStyle.primary)
    async def _open(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        await interaction.response.send_modal(RegistrationModal(bot=self._bot))


def register(bot: discord.Client, tree: app_commands.CommandTree) -> None:
    """
    Onboarding commands.

    Provides:
      - /start     (onboarding + next steps + “Complete Registration” button)
      - /register  (collect Name + ZIP to place volunteer geographically)
      - /whoami    (identity details used for linking/logging)

    Backend Contract:
      - POST /people/discord/upsert   (ensure_person_by_discord)
      - POST /people/onboard
      - POST /people/discord/register (Name + ZIP placement + onboard milestone)
    """

    @tree.command(name="register", description="Complete volunteer registration (Name + ZIP).")
    async def register_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RegistrationModal(bot=bot))

    @tree.command(name="start", description="Start here: onboarding + next steps.")
    async def start(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        api = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send(_onboarding_message_fallback(), ephemeral=True)
            return

        # Link/upsert (this anchors person_id for later actions/logs)
        person_id, person_data, err = await ensure_person_by_discord(bot, interaction)

        if err and person_id is None:
            await interaction.followup.send(
                _onboarding_message_fallback()
                + "\n\n⚠️ Note: I couldn't link you to the dashboard API right now, but you can still follow the steps above.",
                ephemeral=True,
            )
            return

        onboard_payload: Dict[str, Any] = {
            "person_id": person_id,
            "discord_user_id": str(interaction.user.id),
            **_discord_context(interaction),
        }
        onboard_payload = {k: v for k, v in onboard_payload.items() if v is not None}

        code, text, data = await api_request(api, "POST", "/people/onboard", json=onboard_payload, timeout=20)

        if code != 200 or not isinstance(data, dict):
            stage: Optional[str] = None
            if isinstance(person_data, dict):
                try:
                    maybe = person_data.get("stage")
                    stage = maybe if isinstance(maybe, str) else None
                except Exception:
                    stage = None

            msg = _onboarding_message_fallback()
            msg += "\n\n---\n"
            if person_id:
                msg += f"🆔 Linked person_id: **{person_id}**\n"
            msg += "\nNext step:\n" + next_step_for_stage(stage)
            msg += "\n\n⚠️ Note: I couldn't complete onboarding in the API yet.\n"
            msg += format_api_error(code, text, data)

            await interaction.followup.send(msg, ephemeral=True)
            return

        p = data.get("person") or {}
        next_steps = data.get("next_steps") or []

        stage_val: Optional[str] = None
        resolved_id = person_id
        if isinstance(p, dict):
            try:
                stage_val = p.get("stage") if isinstance(p.get("stage"), str) else None
            except Exception:
                stage_val = None
            try:
                pid = p.get("id")
                resolved_id = pid if isinstance(pid, int) else resolved_id
            except Exception:
                pass

        msg_lines: List[str] = [
            "✅ You’re onboarded.",
            f"🆔 person_id: **{resolved_id}**",
        ]
        if stage_val:
            msg_lines.append(f"📍 Stage: **{stage_val.upper()}**")

        msg_lines.append("")
        msg_lines.append("Next steps:")
        formatted = _format_next_steps(next_steps)
        msg_lines.append(formatted if formatted else next_step_for_stage(stage_val))

        msg_lines.append("")
        msg_lines.append("Now finish registration so we can place you geographically:")
        msg_lines.append("➡️ Click **Complete Registration (Name + ZIP)** below, or run `/register`.")

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

        await interaction.followup.send(
            "\n".join(msg_lines),
            view=RegistrationLaunchView(bot=bot),
            ephemeral=True,
        )

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
