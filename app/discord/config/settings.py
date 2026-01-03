from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse


# ---------------------------------------------------------------------
# Operator Readiness: best-effort .env loading for the bot process
# ---------------------------------------------------------------------
def _load_dotenv_if_present() -> None:
    """
    Best-effort dotenv loader (no third-party dependency).

    Why:
    - Bot settings read directly from os.environ.
    - Operators commonly expect ".env" in repo root to be honored.
    - Some shells/tools do not auto-load .env.

    Rules:
    - If a variable is already set in the environment, do NOT override it.
    - Ignore malformed lines.
    - Supports simple KEY=VALUE (no export, no multiline).
    """
    # Allow operators to disable this behavior explicitly.
    if (os.getenv("DASHBOARD_DOTENV_DISABLE", "") or "").strip().lower() in ("1", "true", "yes", "y", "on"):
        return

    candidates = [
        os.getenv("DASHBOARD_DOTENV_PATH", "").strip(),
        ".env",
    ]
    paths = [p for p in candidates if p]

    for path in paths:
        try:
            if not os.path.exists(path):
                continue

            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f.readlines():
                    line = (raw_line or "").strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue

                    k, v = line.split("=", 1)
                    key = (k or "").strip()
                    if not key:
                        continue

                    # Strip optional quotes (common in .env)
                    val = (v or "").strip().strip("'").strip('"')

                    # Do not override real environment values
                    if os.getenv(key) is None:
                        os.environ[key] = val

            # Only load the first existing candidate
            return
        except Exception:
            # Never block boot if dotenv parsing fails.
            return


# Load .env early so Settings() sees the values.
_load_dotenv_if_present()


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


def _norm_str(raw: str, fallback: str) -> str:
    s = (raw or "").strip()
    return s if s else fallback


def _norm_url(raw: str) -> str:
    """
    Normalize a base URL:
    - Strips whitespace
    - Strips trailing slashes
    """
    return (raw or "").strip().rstrip("/")


def _validate_base_url(name: str, value: str) -> None:
    if not value:
        raise RuntimeError(f"{name} is empty/invalid.")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"{name} must start with http:// or https://")
    if not parsed.netloc:
        raise RuntimeError(f"{name} must include a host (and optional port).")


def _validate_log_level(value: str) -> None:
    allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
    v = (value or "").strip().upper()
    if v not in allowed:
        raise RuntimeError(f"LOG_LEVEL must be one of: {', '.join(sorted(allowed))}")


def _validate_timeout(value: float) -> None:
    # Fail-closed: protect against "0" or absurd timeouts that can wedge the bot.
    if value <= 0:
        raise RuntimeError("DASHBOARD_HTTP_TIMEOUT must be > 0.")
    if value > 120:
        raise RuntimeError("DASHBOARD_HTTP_TIMEOUT is too high (max 120s).")


def _validate_discord_guild_id(value: Optional[int]) -> None:
    # Discord snowflakes are up to ~19 digits; allow None.
    if value is None:
        return
    if value <= 0:
        raise RuntimeError("DISCORD_GUILD_ID must be a positive integer.")


def _validate_channel_name(name: str, value: str) -> None:
    # Keep permissive but avoid empty strings (which break UX routing).
    if not value or not value.strip():
        raise RuntimeError(f"{name} must not be empty.")
    if len(value.strip()) > 100:
        raise RuntimeError(f"{name} is too long (max 100 chars).")


def _validate_emoji(value: str) -> None:
    # Keep it short to avoid accidental spam / misuse.
    if not value:
        raise RuntimeError("DASHBOARD_WINS_TRIGGER_EMOJI must not be empty.")
    if len(value) > 32:
        raise RuntimeError("DASHBOARD_WINS_TRIGGER_EMOJI is too long (max 32 chars).")


def _validate_user_agent(value: str) -> None:
    if not value or not value.strip():
        raise RuntimeError("DASHBOARD_HTTP_USER_AGENT must not be empty.")
    if len(value.strip()) > 256:
        raise RuntimeError("DASHBOARD_HTTP_USER_AGENT is too long (max 256 chars).")


def _parse_role_list(raw: str) -> List[str]:
    """
    Parse a comma-separated list of role names or role IDs.
    Empty entries are discarded; whitespace trimmed.
    """
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def _redact(value: str) -> str:
    """
    Minimal redaction for secrets.
    Keeps length context without exposing content.
    """
    if not value:
        return ""
    # Keep very short secrets fully hidden
    if len(value) <= 8:
        return "***"
    # Keep prefix/suffix for troubleshooting "wrong token pasted" cases
    return value[:3] + "***" + value[-3:]


@dataclass(frozen=True)
class Settings:
    """
    Bot settings (Discord control plane).

    Phase 5.2 rules:
    - Immutable once loaded (avoid runtime drift)
    - Fail fast on invalid configuration
    - Keep optional features optional; keep core requirements strict

    Operator Readiness notes (Phase 5.3):
    - This class reads from environment variables.
    - We best-effort load `.env` if present (unless DASHBOARD_DOTENV_DISABLE=1).
    """

    # -------------------------
    # Core app / logging
    # -------------------------
    log_level: str = _env("LOG_LEVEL", "INFO").strip().upper() or "INFO"

    # -------------------------
    # Discord bot
    # -------------------------
    discord_bot_token: str = _env("DISCORD_BOT_TOKEN", "").strip()
    discord_guild_id: Optional[int] = _env_int("DISCORD_GUILD_ID", None)

    # API base used by the Discord bot to call the dashboard backend
    dashboard_api_base: str = _norm_url(_env("DASHBOARD_API_BASE", "http://127.0.0.1:8000"))

    # Slash-command sync behavior
    # If True, sync commands to a single guild (fast iteration). Requires DISCORD_GUILD_ID.
    discord_sync_guild_only: bool = _env_bool("DISCORD_SYNC_GUILD_ONLY", True)

    # UX routing channels (Discord channel names)
    wins_channel_name: str = _norm_str(_env("DASHBOARD_WINS_CHANNEL", "wins-and-updates"), "wins-and-updates")
    first_actions_channel_name: str = _norm_str(_env("DASHBOARD_FIRST_ACTIONS_CHANNEL", "first-actions"), "first-actions")

    # Bot feature flags (Phase 4/5: Discord as control plane)
    enable_wins_automation: bool = _env_bool("DASHBOARD_ENABLE_WINS_AUTOMATION", True)
    wins_trigger_emoji: str = (_env("DASHBOARD_WINS_TRIGGER_EMOJI", "✅").strip() or "✅")
    wins_require_channel: bool = _env_bool("DASHBOARD_WINS_REQUIRE_CHANNEL", True)

    enable_role_sync: bool = _env_bool("DASHBOARD_ENABLE_ROLE_SYNC", True)
    enable_training_system: bool = _env_bool("DASHBOARD_ENABLE_TRAINING_SYSTEM", True)

    # Discord role names to apply on approval (customizable per server)
    role_team: str = _norm_str(_env("DASHBOARD_ROLE_TEAM", "Team"), "Team")
    role_fundraising: str = _norm_str(_env("DASHBOARD_ROLE_FUNDRAISING", "Fundraising"), "Fundraising")
    role_leader: str = _norm_str(_env("DASHBOARD_ROLE_LEADER", "Leader"), "Leader")

    # Discord-side guard roles (names or IDs). If empty, fall back to guild permissions.
    admin_roles_raw: str = _env("DASHBOARD_ADMIN_ROLES", "").strip()
    lead_roles_raw: str = _env("DASHBOARD_LEAD_ROLES", "").strip()

    # Optional public links used in onboarding responses
    onboarding_url: str = _env("DASHBOARD_ONBOARDING_URL", "").strip()
    volunteer_form_url: str = _env("DASHBOARD_VOLUNTEER_FORM_URL", "").strip()
    discord_help_url: str = _env("DASHBOARD_DISCORD_HELP_URL", "").strip()

    # HTTP
    http_timeout_s: float = _env_float("DASHBOARD_HTTP_TIMEOUT", 20.0)
    http_user_agent: str = _norm_str(
        _env("DASHBOARD_HTTP_USER_AGENT", "campaign-dashboard-discord-bot/1.0"),
        "campaign-dashboard-discord-bot/1.0",
    )

    # -------------------------
    # External API keys (optional)
    # -------------------------
    census_api_key: str = _env("CENSUS_API_KEY", "").strip()
    bls_api_key: str = _env("BLS_API_KEY", "").strip()

    # -------------------------
    # Convenience (operator-friendly)
    # -------------------------
    @property
    def admin_roles(self) -> List[str]:
        return _parse_role_list(self.admin_roles_raw)

    @property
    def lead_roles(self) -> List[str]:
        return _parse_role_list(self.lead_roles_raw)

    def redacted_dict(self) -> Dict[str, Any]:
        """
        Safe snapshot for logs/diagnostics (no raw secrets).
        """
        return {
            "log_level": self.log_level,
            "discord_bot_token": _redact(self.discord_bot_token),
            "discord_guild_id": self.discord_guild_id,
            "dashboard_api_base": self.dashboard_api_base,
            "discord_sync_guild_only": self.discord_sync_guild_only,
            "wins_channel_name": self.wins_channel_name,
            "first_actions_channel_name": self.first_actions_channel_name,
            "enable_wins_automation": self.enable_wins_automation,
            "wins_trigger_emoji": self.wins_trigger_emoji,
            "wins_require_channel": self.wins_require_channel,
            "enable_role_sync": self.enable_role_sync,
            "enable_training_system": self.enable_training_system,
            "role_team": self.role_team,
            "role_fundraising": self.role_fundraising,
            "role_leader": self.role_leader,
            "admin_roles": self.admin_roles,
            "lead_roles": self.lead_roles,
            "onboarding_url": self.onboarding_url,
            "volunteer_form_url": self.volunteer_form_url,
            "discord_help_url": self.discord_help_url,
            "http_timeout_s": self.http_timeout_s,
            "http_user_agent": self.http_user_agent,
            "census_api_key": _redact(self.census_api_key),
            "bls_api_key": _redact(self.bls_api_key),
        }

    def diagnostics(self) -> Dict[str, Any]:
        """
        Operator-focused diagnostics that avoid secrets.
        This is safe to log.
        """
        token_ok = bool(self.discord_bot_token)
        guild_ok = bool(self.discord_guild_id) if self.discord_sync_guild_only else True

        return {
            "token_present": token_ok,
            "sync_mode": "guild_only" if self.discord_sync_guild_only else "global",
            "guild_id_present": bool(self.discord_guild_id),
            "guild_required_ok": guild_ok,
            "api_base": self.dashboard_api_base,
            "wins_enabled": self.enable_wins_automation,
            "wins_channel_required": self.wins_require_channel,
            "role_sync_enabled": self.enable_role_sync,
            "training_enabled": self.enable_training_system,
        }

    def validate(self) -> None:
        """
        Strict validation for boot safety.
        """
        # Required secret
        if not self.discord_bot_token:
            raise RuntimeError(
                "DISCORD_BOT_TOKEN is not set in environment.\n"
                "Operator fix:\n"
                "  - Ensure a .env exists in the repo root and contains DISCORD_BOT_TOKEN=...\n"
                "  - Or set it in the shell:  $env:DISCORD_BOT_TOKEN='...'\n"
                "  - If using a different .env path: set DASHBOARD_DOTENV_PATH='path/to/.env'\n"
                "  - To disable .env loading: set DASHBOARD_DOTENV_DISABLE=1\n"
            )

        # Core URL validation
        _validate_base_url("DASHBOARD_API_BASE", self.dashboard_api_base)

        # Logging validation
        _validate_log_level(self.log_level)

        # Discord guild validation
        _validate_discord_guild_id(self.discord_guild_id)

        # Sync discipline: guild-only sync requires a guild id
        if self.discord_sync_guild_only and not self.discord_guild_id:
            raise RuntimeError("DISCORD_SYNC_GUILD_ONLY is true but DISCORD_GUILD_ID is not set.")

        # First-actions channel is used by onboarding messaging; keep strict.
        _validate_channel_name("DASHBOARD_FIRST_ACTIONS_CHANNEL", self.first_actions_channel_name)

        # Wins automation should not brick boot if disabled.
        if self.enable_wins_automation:
            if self.wins_require_channel:
                _validate_channel_name("DASHBOARD_WINS_CHANNEL", self.wins_channel_name)
            _validate_emoji(self.wins_trigger_emoji)

        # HTTP constraints
        _validate_timeout(self.http_timeout_s)
        _validate_user_agent(self.http_user_agent)


settings = Settings()

__all__ = ["Settings", "settings"]
