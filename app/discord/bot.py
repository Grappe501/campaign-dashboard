from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import discord
import httpx
from discord import app_commands

from .commands import register_all
from .commands.impact import format_log_suggestion
from .commands.shared import api_request, infer_channel_from_action_type
from .config import settings

logger = logging.getLogger(__name__)

_INT_RE = re.compile(r"(?<!\d)(\d{1,6})(?!\d)")  # up to 6 digits for safety


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _infer_action_type_from_text(text: str) -> str:
    """
    Conservative heuristic mapping message text -> ImpactAction.action_type.
    Must align with backend rule keys (DEFAULT_RULES) where possible.
    """
    t = _norm(text)

    if "sign up" in t or "signup" in t or "registered" in t:
        return "signup"

    if "call" in t or "dial" in t:
        return "call"

    if "text" in t or "sms" in t:
        return "text"

    if "door" in t or "knock" in t or "canvass" in t:
        return "door"

    if "hosted" in t and ("event" in t or "meeting" in t or "rally" in t):
        return "event_hosted"

    if "event" in t or "meeting" in t or "rally" in t:
        return "event_attended"

    if "share" in t or "shared" in t or "post" in t or "posted" in t or "social" in t:
        return "post_shared"

    # Default safe guess (still editable via /log)
    return "call"


def _infer_quantity_from_text(text: str) -> int:
    """
    Parse first integer if present; else default 1.
    Clamp to a reasonable max to avoid abuse / accidental huge numbers.
    """
    m = _INT_RE.search(text or "")
    if not m:
        return 1
    try:
        n = int(m.group(1))
    except Exception:
        return 1
    if n < 1:
        return 1
    if n > 10_000:
        return 10_000
    return n


def _wins_reply_template(action_type: str, qty: int) -> str:
    # Short, non-spammy, “do this next” nudge.
    return "Nice ✅\n" f"Log it with `{format_log_suggestion(action_type, qty)}` (edit as needed)."


def _parse_channel_ref(raw: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Accept either:
      - numeric channel id
      - channel name (no #)
      - "#channel-name"
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.startswith("#"):
        s = s[1:].strip()
    if s.isdigit():
        try:
            return int(s), None
        except Exception:
            return None, None
    return None, s


async def _ensure_person_from_discord_message(
    api: httpx.AsyncClient,
    message: discord.Message,
) -> Tuple[Optional[int], Optional[dict], Optional[str]]:
    """
    Minimal “upsert from discord” without importing interaction helpers.
    Uses backend endpoint: POST /people/discord/upsert
    """
    payload = {
        "discord_user_id": str(message.author.id),
        "name": getattr(message.author, "display_name", "") or getattr(message.author, "name", ""),
        "last_seen_discord_guild_id": str(message.guild.id) if message.guild else None,
        "last_seen_discord_channel_id": str(getattr(message.channel, "id", "")) if getattr(message, "channel", None) else None,
        "last_seen_discord_username": str(message.author),
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    code, text, data = await api_request(api, "POST", "/people/discord/upsert", json=payload, timeout=15)
    if code != 200 or not isinstance(data, dict):
        logger.warning("wins: person upsert failed code=%s text=%s", code, (text or "")[:200])
        return None, None, "person_upsert_failed"

    pid = data.get("id")
    if isinstance(pid, int):
        return pid, data, None
    if isinstance(pid, str) and pid.isdigit():
        return int(pid), data, None
    return None, data, "missing_person_id"


async def _auto_log_impact_action(
    api: httpx.AsyncClient,
    *,
    actor_person_id: int,
    action_type: str,
    quantity: int,
    idempotency_key: str,
    meta: Dict[str, Any],
) -> bool:
    """
    Create an ImpactAction using the real backend endpoint/schema:
      POST /impact/actions
    """
    payload: Dict[str, Any] = {
        "action_type": action_type,
        "quantity": int(quantity),
        "actor_person_id": int(actor_person_id),
        "source": "discord",
        "channel": infer_channel_from_action_type(action_type),
        "idempotency_key": idempotency_key,
        "meta": meta,
    }

    code, _, data = await api_request(api, "POST", "/impact/actions", json=payload, timeout=20)
    if code in (404, 405):
        # Backend might not include impact module in some deployments; treat as "not logged"
        return False
    if code == 200 and isinstance(data, dict):
        return True

    if code >= 400:
        logger.warning("wins: impact auto-log failed (code=%s)", code)
    return False


class DashboardBot(discord.Client):
    """
    Discord control-plane bot for the Campaign Dashboard.

    Notes:
    - discord.Client uses its own internal HTTP client for Discord.
    - We keep a separate httpx.AsyncClient as self.api for dashboard backend calls.
    - All bot->backend HTTP calls must go through commands.shared.api_request.
    """

    # Wins debounce / cache controls
    _WINS_DEBOUNCE_TTL_S = 600  # seconds
    _WINS_CACHE_SOFT_LIMIT = 5000  # prune when above this
    _WINS_CACHE_HARD_LIMIT = 20000  # absolute cap (safety)

    # Optional reply throttle (avoid “dogpiling” in busy channels)
    _WINS_REPLY_TTL_S = 45  # seconds per author per channel

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

        # Reply throttle cache: "guild:channel:author" -> unix ts
        self._wins_recent_replies: Dict[str, float] = {}

        # --- Wins routing config (operator-friendly; env-driven) ---
        # Forwarding is optional: set DASHBOARD_WINS_FORWARD_CHANNEL to a channel id or name.
        self._wins_forward_channel_id, self._wins_forward_channel_name = _parse_channel_ref(
            _env("DASHBOARD_WINS_FORWARD_CHANNEL", "")
        )

        # Feature toggles inside wins automation (defaults match your choice "E")
        self._wins_react_enabled = _env_bool("DASHBOARD_WINS_REACT", True)
        self._wins_reply_enabled = _env_bool("DASHBOARD_WINS_REPLY", True)
        self._wins_autolog_enabled = _env_bool("DASHBOARD_WINS_AUTOLOG", True)
        self._wins_forward_enabled = _env_bool("DASHBOARD_WINS_FORWARD", True)

        # Optional: also add 🎉 reaction
        self._wins_party_react = _env_bool("DASHBOARD_WINS_REACT_PARTY", True)

    def _sync_mode_label(self) -> str:
        if settings.discord_sync_guild_only and settings.discord_guild_id:
            return f"guild_only:{settings.discord_guild_id}"
        return "global"

    async def setup_hook(self) -> None:
        """
        Runs once during startup before on_ready.
        Used for:
        - creating the backend http client
        - registering slash commands
        - syncing commands (guild-only or global)
        """
        if self.api is None:
            # Keep defaults conservative; dashboard API is internal and should be reliable.
            limits = httpx.Limits(max_connections=25, max_keepalive_connections=10)
            self.api = httpx.AsyncClient(
                timeout=float(settings.http_timeout_s),
                headers={"User-Agent": settings.http_user_agent},
                limits=limits,
                follow_redirects=True,
            )

        # Register slash commands from modular command files (may raise if required modules fail)
        register_all(self, self.tree)

        # Sync commands (guild-only optional for fast iteration)
        try:
            guild_id = settings.discord_guild_id
            if guild_id and settings.discord_sync_guild_only:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced (mode=guild_only guild_id=%s)", guild_id)
            else:
                await self.tree.sync()
                logger.info("Slash commands synced (mode=global)")
        except Exception:
            # Sync errors should be visible to operators but should not crash startup.
            logger.exception("Slash command sync failed (mode=%s)", self._sync_mode_label())

    async def close(self) -> None:
        # Close backend http client first
        if self.api is not None:
            try:
                await self.api.aclose()
            except Exception:
                logger.exception("Error closing backend http client (ignored).")
            self.api = None
        await super().close()

    async def on_ready(self) -> None:
        # Safe operator snapshot (no secrets) if available
        redacted = None
        if hasattr(settings, "redacted_dict"):
            try:
                redacted = settings.redacted_dict()
            except Exception:
                redacted = None

        logger.info(
            "DashboardBot ready as %s (sync=%s wins=%s role_sync=%s api_base=%s)",
            str(self.user),
            self._sync_mode_label(),
            "ON" if settings.enable_wins_automation else "OFF",
            "ON" if settings.enable_role_sync else "OFF",
            settings.dashboard_api_base.rstrip("/"),
        )
        if redacted:
            logger.info("Bot settings (redacted): %s", redacted)

        if settings.enable_wins_automation:
            logger.info(
                "wins routing: react=%s reply=%s autolog=%s forward=%s forward_channel=%s",
                "ON" if self._wins_react_enabled else "OFF",
                "ON" if self._wins_reply_enabled else "OFF",
                "ON" if self._wins_autolog_enabled else "OFF",
                "ON" if self._wins_forward_enabled else "OFF",
                (
                    str(self._wins_forward_channel_id)
                    if self._wins_forward_channel_id
                    else (self._wins_forward_channel_name or "(unset)")
                ),
            )

    def _wins_cache_prune(self, now_ts: float) -> None:
        """
        Prune old debounce keys so memory doesn't grow forever.
        Only triggers once cache grows beyond a soft limit.
        """
        if not self._wins_recent_keys and not self._wins_recent_replies:
            return

        # debounce keys prune
        if len(self._wins_recent_keys) >= self._WINS_CACHE_SOFT_LIMIT:
            cutoff = now_ts - float(self._WINS_DEBOUNCE_TTL_S)
            self._wins_recent_keys = {k: ts for k, ts in self._wins_recent_keys.items() if ts >= cutoff}

            # Hard safety: if we ever balloon, trim deterministically.
            if len(self._wins_recent_keys) > self._WINS_CACHE_HARD_LIMIT:
                logger.warning("wins debounce cache exceeded hard limit (%s); trimming", self._WINS_CACHE_HARD_LIMIT)
                items = sorted(self._wins_recent_keys.items(), key=lambda kv: kv[1], reverse=True)
                self._wins_recent_keys = dict(items[: self._WINS_CACHE_HARD_LIMIT])

        # reply throttle prune (simple sweep)
        if self._wins_recent_replies:
            cutoff2 = now_ts - float(self._WINS_REPLY_TTL_S)
            self._wins_recent_replies = {k: ts for k, ts in self._wins_recent_replies.items() if ts >= cutoff2}

    async def _wins_forward_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """
        Resolve the forwarding channel if configured.
        """
        if not self._wins_forward_enabled:
            return None

        if not self._wins_forward_channel_id and not self._wins_forward_channel_name:
            return None

        ch: Optional[discord.abc.GuildChannel] = None

        if self._wins_forward_channel_id:
            ch = guild.get_channel(self._wins_forward_channel_id)

        if ch is None and self._wins_forward_channel_name:
            target = _norm(self._wins_forward_channel_name)
            for c in guild.channels:
                if isinstance(c, discord.TextChannel) and _norm(c.name) == target:
                    ch = c
                    break

        return ch if isinstance(ch, discord.TextChannel) else None

    async def on_message(self, message: discord.Message) -> None:
        """
        Wins automation (E bundle):
        - Watches for trigger emoji in messages (optionally only in a specific channel)
        - Reacts + replies + auto-logs + forwards (all best-effort)
        """
        if not settings.enable_wins_automation:
            return

        trigger = (settings.wins_trigger_emoji or "").strip()
        if not trigger:
            return

        try:
            # Ignore bots, DMs, and non-guild contexts
            if message.author.bot:
                return
            if not message.guild:
                return

            # Optional channel scoping
            if settings.wins_require_channel:
                if not isinstance(message.channel, discord.TextChannel):
                    return
                if _norm(message.channel.name or "") != _norm(settings.wins_channel_name or ""):
                    return

            content = (message.content or "").strip()
            if not content or (trigger not in content):
                return

            # Debounce: avoid double-processing the same message
            key = f"{message.guild.id}:{message.channel.id}:{message.id}"
            now = time.time()
            self._wins_cache_prune(now)

            last = self._wins_recent_keys.get(key)
            if last and (now - last) < float(self._WINS_DEBOUNCE_TTL_S):
                return
            self._wins_recent_keys[key] = now

            inferred_action = _infer_action_type_from_text(content)
            inferred_qty = _infer_quantity_from_text(content)

            # 1) React
            if self._wins_react_enabled:
                try:
                    await message.add_reaction(trigger)
                    if self._wins_party_react:
                        await message.add_reaction("🎉")
                except Exception:
                    logger.info("wins: could not add reaction(s) (ignored)", exc_info=True)

            # 2) Reply (brief nudge) — with throttle
            if self._wins_reply_enabled:
                try:
                    rkey = f"{message.guild.id}:{message.channel.id}:{message.author.id}"
                    rlast = self._wins_recent_replies.get(rkey)
                    if not rlast or (now - rlast) >= float(self._WINS_REPLY_TTL_S):
                        await message.reply(_wins_reply_template(inferred_action, inferred_qty), mention_author=False)
                        self._wins_recent_replies[rkey] = now
                except Exception:
                    logger.info("wins: could not reply (ignored)", exc_info=True)

            if self.api is None:
                return

            # 3) Ensure Person exists (discord upsert)
            person_id, _, perr = await _ensure_person_from_discord_message(self.api, message)

            # 4) Auto-log to Impact (if we could link/create the person)
            logged = False
            if self._wins_autolog_enabled and not perr and person_id is not None:
                discord_meta = {
                    "discord": {
                        "guild_id": str(message.guild.id),
                        "channel_id": str(message.channel.id),
                        "channel_name": getattr(message.channel, "name", None),
                        "message_id": str(message.id),
                        "message_url": message.jump_url,
                        "author_discord_id": str(message.author.id),
                        "author_name": getattr(message.author, "display_name", "")
                        or getattr(message.author, "name", ""),
                    },
                    "win_text": (content[:1200]),
                    "trigger_emoji": trigger,
                }

                # Dedupe per-message (so restarts don't double count)
                idem = f"discord_win:{message.guild.id}:{message.channel.id}:{message.id}"

                try:
                    logged = await _auto_log_impact_action(
                        self.api,
                        actor_person_id=int(person_id),
                        action_type=inferred_action,
                        quantity=inferred_qty,
                        idempotency_key=idem,
                        meta=discord_meta,
                    )
                except Exception:
                    logger.exception("wins: impact auto-log error (ignored)")
                    logged = False

            # 5) Forward to leader channel
            if self._wins_forward_enabled:
                try:
                    leader_ch = await self._wins_forward_channel(message.guild)
                    if leader_ch:
                        who = getattr(message.author, "display_name", "") or getattr(message.author, "name", "someone")
                        excerpt = content if len(content) <= 800 else (content[:797] + "...")
                        status = "logged" if logged else "not_logged"
                        await leader_ch.send(
                            f"🏁 **Win** ({status}) from **{who}** in <#{message.channel.id}>:\n"
                            f"{excerpt}\n"
                            f"↪ {message.jump_url}"
                        )
                except Exception:
                    logger.info("wins: forward failed (ignored)", exc_info=True)

            return

        except Exception:
            # Never let automation break normal bot operation
            logger.exception("Wins automation error (ignored).")
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
