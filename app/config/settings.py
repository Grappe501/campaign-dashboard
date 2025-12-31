from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = _env(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _norm_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def _norm_role_name(raw: str, fallback: str) -> str:
    s = (raw or "").strip()
    return s if s else fallback


@dataclass(frozen=True)
class Settings:
    # -------------------------
    # Core app / logging
    # -------------------------
    log_level: str = _env("LOG_LEVEL", "INFO").strip() or "INFO"

    # -------------------------
    # Discord bot
    # -------------------------
    discord_bot_token: str = _env("DISCORD_BOT_TOKEN", "").strip()
    discord_guild_id: Optional[int] = _env_int("DISCORD_GUILD_ID", None)

    # API base used by the Discord bot to call the dashboard backend
    dashboard_api_base: str = _norm_url(_env("DASHBOARD_API_BASE", "http://127.0.0.1:8000"))

    # Slash-command sync behavior
    discord_sync_guild_only: bool = _env_bool("DISCORD_SYNC_GUILD_ONLY", True)

    # UX routing channels (Discord channel names)
    wins_channel_name: str = _env("DASHBOARD_WINS_CHANNEL", "wins-and-updates").strip() or "wins-and-updates"
    first_actions_channel_name: str = _env("DASHBOARD_FIRST_ACTIONS_CHANNEL", "first-actions").strip() or "first-actions"

    # Bot feature flags (Phase 4: Discord as control plane)
    enable_wins_automation: bool = _env_bool("DASHBOARD_ENABLE_WINS_AUTOMATION", True)
    wins_trigger_emoji: str = (_env("DASHBOARD_WINS_TRIGGER_EMOJI", "✅").strip() or "✅")
    wins_require_channel: bool = _env_bool("DASHBOARD_WINS_REQUIRE_CHANNEL", True)

    enable_role_sync: bool = _env_bool("DASHBOARD_ENABLE_ROLE_SYNC", True)
    enable_training_system: bool = _env_bool("DASHBOARD_ENABLE_TRAINING_SYSTEM", True)

    # Discord role names to apply on approval (can be customized per server)
    role_team: str = _norm_role_name(_env("DASHBOARD_ROLE_TEAM", "Team"), "Team")
    role_fundraising: str = _norm_role_name(_env("DASHBOARD_ROLE_FUNDRAISING", "Fundraising"), "Fundraising")
    role_leader: str = _norm_role_name(_env("DASHBOARD_ROLE_LEADER", "Leader"), "Leader")

    # Discord-side guard roles (names or IDs). If empty, fall back to guild permissions.
    admin_roles_raw: str = _env("DASHBOARD_ADMIN_ROLES", "").strip()
    lead_roles_raw: str = _env("DASHBOARD_LEAD_ROLES", "").strip()

    # Optional public links used in onboarding responses
    onboarding_url: str = _env("DASHBOARD_ONBOARDING_URL", "").strip()
    volunteer_form_url: str = _env("DASHBOARD_VOLUNTEER_FORM_URL", "").strip()
    discord_help_url: str = _env("DASHBOARD_DISCORD_HELP_URL", "").strip()

    # HTTP
    http_timeout_s: float = _env_float("DASHBOARD_HTTP_TIMEOUT", 20.0)
    http_user_agent: str = _env("DASHBOARD_HTTP_USER_AGENT", "campaign-dashboard-discord-bot/1.0").strip() or "campaign-dashboard-discord-bot/1.0"

    # -------------------------
    # External API keys (optional)
    # -------------------------
    census_api_key: str = _env("CENSUS_API_KEY", "").strip()
    bls_api_key: str = _env("BLS_API_KEY", "").strip()

    def validate(self) -> None:
        """
        Keep validation strict for secrets, lenient for optional Phase 4 toggles.
        """
        if not self.discord_bot_token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set in environment (.env).")

        if not self.dashboard_api_base:
            raise RuntimeError("DASHBOARD_API_BASE is empty/invalid.")

        # wins_trigger_emoji must be a short string to avoid accidental spam
        if len(self.wins_trigger_emoji) > 32:
            raise RuntimeError("DASHBOARD_WINS_TRIGGER_EMOJI is too long (max 32 chars).")


settings = Settings()
