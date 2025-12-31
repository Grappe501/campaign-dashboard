from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Dict, Optional

import discord
import httpx
from discord import app_commands

from ..config.settings import settings
from .commands import register_all
from .commands.shared import api_request  # single source of truth for bot->API calls

logger = logging.getLogger(__name__)


class DashboardBot(discord.Client):
    """
    Discord control-plane bot for the Campaign Dashboard.

    Notes:
    - discord.Client uses its own internal HTTP client for Discord.
    - We keep a separate httpx AsyncClient as self.api for dashboard backend calls.
    """

    # Wins debounce / cache controls
    _WINS_DEBOUNCE_TTL_S = 600  # seconds
    _WINS_CACHE_SOFT_LIMIT = 5000  # prune when above this
    _WINS_CACHE_HARD_LIMIT = 20000  # absolute cap (safety)

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True

        # Needed for /sync_me and approvals role application
        if settings.enable_role_sync:
            intents.members = True

        # Needed for wins automation (listening for trigger emoji in messages)
        if settings.enable_wins_automation:
            intents.message_content = True

        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.api: Optional[httpx.AsyncClient] = None

        # Debounce cache for wins automation: key -> unix timestamp (seconds)
        self._wins_recent_keys: Dict[str, float] = {}

    async def setup_hook(self) -> None:
        # Initialize API client once
        if self.api is None:
            self.api = httpx.AsyncClient(
                timeout=float(settings.http_timeout_s),
                headers={"User-Agent": settings.http_user_agent},
            )

        # Register slash commands from modular command files
        register_all(self, self.tree)

        # Sync commands (guild-only optional for fast iteration)
        try:
            guild_id = settings.discord_guild_id
            if guild_id and settings.discord_sync_guild_only:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced to guild=%s", guild_id)
            else:
                await self.tree.sync()
                logger.info("Slash commands synced globally")
        except Exception:
            logger.exception("Slash command sync failed")

    async def close(self) -> None:
        # Close backend http client first
        if self.api is not None:
            try:
                await self.api.aclose()
            except Exception:
                pass
            self.api = None
        await super().close()

    async def on_ready(self) -> None:
        logger.info(
            "DashboardBot ready as %s (guild_sync=%s, wins=%s, role_sync=%s, api=%s)",
            str(self.user),
            str(settings.discord_guild_id or "global"),
            "ON" if settings.enable_wins_automation else "OFF",
            "ON" if settings.enable_role_sync else "OFF",
            settings.dashboard_api_base.rstrip("/"),
        )

    def _wins_cache_prune(self, now_ts: float) -> None:
        """
        Prune old debounce keys so memory doesn't grow forever.
        Only triggers once cache grows beyond a soft limit.
        """
        if not self._wins_recent_keys:
            return

        # Hard safety: if we ever balloon, trim after pruning.
        if len(self._wins_recent_keys) >= self._WINS_CACHE_HARD_LIMIT:
            logger.warning(
                "wins debounce cache exceeded hard limit (%s); trimming",
                self._WINS_CACHE_HARD_LIMIT,
            )

        if len(self._wins_recent_keys) < self._WINS_CACHE_SOFT_LIMIT:
            return

        cutoff = now_ts - float(self._WINS_DEBOUNCE_TTL_S)
        pruned = {k: ts for k, ts in self._wins_recent_keys.items() if ts >= cutoff}
        self._wins_recent_keys = pruned

        # If still huge (e.g., high-traffic server in <TTL window), trim deterministically.
        if len(self._wins_recent_keys) > self._WINS_CACHE_HARD_LIMIT:
            # Keep the most recent timestamps
            items = sorted(self._wins_recent_keys.items(), key=lambda kv: kv[1], reverse=True)
            self._wins_recent_keys = dict(items[: self._WINS_CACHE_HARD_LIMIT])

    async def on_message(self, message: discord.Message) -> None:
        """
        Phase 4: Wins automation
        - Watches for trigger emoji (optionally only in a specific channel)
        - POSTs to /wins/ingest (best-effort; safe to ignore if endpoint not present)
        """
        if not settings.enable_wins_automation:
            return

        # If misconfigured (empty emoji), do nothing
        trigger = (settings.wins_trigger_emoji or "").strip()
        if not trigger:
            return

        try:
            if message.author.bot or not message.guild:
                return

            if settings.wins_require_channel:
                if not isinstance(message.channel, discord.TextChannel):
                    return
                if (message.channel.name or "").strip().lower() != (settings.wins_channel_name or "").strip().lower():
                    return

            content = (message.content or "").strip()
            if not content or (trigger not in content):
                return

            # Debounce: avoid double ingest
            key = f"{message.guild.id}:{message.channel.id}:{message.id}"
            now = time.time()

            self._wins_cache_prune(now)

            last = self._wins_recent_keys.get(key)
            if last and (now - last) < float(self._WINS_DEBOUNCE_TTL_S):
                return
            self._wins_recent_keys[key] = now

            if self.api is None:
                return

            payload = {
                "discord_user_id": str(message.author.id),
                "discord_message_id": str(message.id),
                "guild_id": str(message.guild.id),
                "channel_id": str(message.channel.id),
                "channel_name": getattr(message.channel, "name", None),
                "content": content[:1200],
                "created_at": (
                    message.created_at.replace(tzinfo=None).isoformat() if message.created_at else None
                ),
                "source": "discord",
                "trigger_emoji": trigger,
            }

            code, _, _ = await api_request(self.api, "POST", "/wins/ingest", json=payload, timeout=10)

            # Graceful: backend might not ship wins endpoint yet
            if code in (404, 405):
                return

            # No user-facing response; wins are “silent ingestion”
            return

        except Exception:
            # Never let automation break normal bot operation
            return


# ---------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------

bot = DashboardBot()


def run_bot() -> None:
    logging.basicConfig(level=getattr(logging, str(settings.log_level).upper(), logging.INFO))
    settings.validate()
    bot.run(settings.discord_bot_token)


if __name__ == "__main__":
    run_bot()
