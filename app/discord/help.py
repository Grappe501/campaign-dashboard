from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from discord import app_commands

from ...config.settings import settings

if TYPE_CHECKING:
    import discord


def _lines(*items: str) -> str:
    return "\n".join([s for s in items if s])


def _safe_url(label: str, url: Optional[str]) -> str:
    if not url:
        return f"- {label}: (not set)"
    return f"- {label}: {url}"


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Help / docs / links.

    Provides:
      - /help   (quick start + command map)
      - /links  (campaign hub links from settings)
    """

    @tree.command(name="help", description="Show the quick-start and command map.")
    async def help_cmd(interaction: "discord.Interaction") -> None:
        wins_ch = settings.wins_channel_name
        first_ch = settings.first_actions_channel_name

        msg = _lines(
            "🧭 **Volunteer Hub Help**",
            "",
            "**Start here**",
            f"1) Read **#{first_ch}** and do one small action",
            "2) Log it with `/log` (example: `/log action_type:call quantity:10`)",
            f"3) Celebrate in **#{wins_ch}** with {settings.wins_trigger_emoji}",
            "",
            "**Most-used commands**",
            "- `/start` — onboarding + next steps",
            "- `/whoami` — your Discord IDs used for linking",
            "- `/log` — log calls/texts/doors/events",
            "- `/reach` — reach summary over a date range",
            "- `/my_next` — get your next suggested action",
            "- `/request_team_access` — request TEAM/FUNDRAISING/LEADER access",
            "- `/trainings` — list trainings (if enabled)",
            "- `/training_complete` — mark a training complete (if enabled)",
            "- `/links` — official links",
            "",
            "**Admin tools (admin-only)**",
            "- `/approvals_pending` — list pending approvals",
            "- `/approve` — approve/deny a request",
            "",
            "Tip: If something errors, it usually means the backend endpoint isn’t live yet.",
        )

        await interaction.response.send_message(msg, ephemeral=True)

    @tree.command(name="links", description="Show official campaign links (onboarding, forms, help).")
    async def links_cmd(interaction: "discord.Interaction") -> None:
        msg = _lines(
            "🔗 **Official Links**",
            _safe_url("Onboarding", settings.onboarding_url),
            _safe_url("Volunteer form", settings.volunteer_form_url),
            _safe_url("Discord help", settings.discord_help_url),
        )
        await interaction.response.send_message(msg, ephemeral=True)
