from __future__ import annotations

import importlib
import logging
import os
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    import discord
    from discord import app_commands

logger = logging.getLogger(__name__)

# Single registry of command modules for the bot.
# Add new modules here as we expand capabilities.
#
# Phase 5.2 hardening:
# - Deterministic module ordering
# - Clear logging of what loaded/registered/failed
# - Fail-closed for required modules (so the bot doesn't come up "half working")
MODULES: Sequence[str] = (
    "core",        # /ping, /config
    "onboarding",  # /start, /whoami
    "impact",      # /log, /reach, /my_next
    "approvals",   # /request_team_access + admin review flows
    "power5",      # power-of-5 commands (optional)
    "trainings",   # trainings + completion (optional)
    "external",    # census/bls proxy lookups (optional)
    "role_sync",   # /sync_me role sync (optional)
)

# Modules that must be present and successfully register for the bot to be considered healthy.
REQUIRED_MODULES: Sequence[str] = (
    "core",
    "onboarding",
    "impact",
    "approvals",
)

__all__ = ["register_all", "MODULES", "REQUIRED_MODULES"]


def _env_list(name: str) -> Optional[Sequence[str]]:
    """
    Optional allow/deny lists for module registration.
    Comma-separated module names.

    Examples:
      DISCORD_COMMANDS_ALLOW=core,onboarding,impact,approvals
      DISCORD_COMMANDS_DENY=external,trainings
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def _should_register(module_name: str) -> bool:
    """
    Apply allow/deny lists if present.
    Defaults to register all MODULES.
    """
    allow = _env_list("DISCORD_COMMANDS_ALLOW")
    deny = _env_list("DISCORD_COMMANDS_DENY")

    if allow is not None:
        return module_name in set(allow)
    if deny is not None:
        return module_name not in set(deny)
    return True


def _import_module(mod_path: str) -> Tuple[Optional[object], Optional[str]]:
    """
    Import a command module safely.

    Returns: (module_or_none, error_string_or_none)

    Rules:
    - ModuleNotFoundError for the module itself => treated as "missing" (non-fatal for optional modules)
    - Any other exception => module exists but failed to import (fatal if required)
    """
    try:
        return importlib.import_module(mod_path), None
    except ModuleNotFoundError as e:
        # Only treat as "missing module" if the missing name is the module itself or its direct subpath.
        # This avoids swallowing genuine dependency errors inside the module.
        missing_name = getattr(e, "name", "") or ""
        if missing_name and (missing_name == mod_path or missing_name.startswith(mod_path + ".")):
            return None, f"missing module: {missing_name}"
        return None, f"import error (dependency missing): {missing_name or str(e)}"
    except Exception as e:
        return None, f"import error: {e}"


def register_all(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Register all command modules with the shared CommandTree.

    Pattern:
      - bot.py stays small/stable (entrypoint doesn't change)
      - commands are split into focused modules under discord/commands/
      - avoids circular imports by passing (bot, tree) explicitly

    Each module must expose:
        def register(bot, tree) -> None

    Phase 5.2 hardening:
      - produce a deterministic registration report in logs
      - fail-closed if any REQUIRED_MODULE fails to import/register
    """
    pkg = __name__  # e.g. "app.discord.commands"
    results: Dict[str, str] = {}
    fatal: List[str] = []

    for name in MODULES:
        if not _should_register(name):
            results[name] = "skipped (allow/deny)"
            continue

        mod_path = f"{pkg}.{name}"
        mod, err = _import_module(mod_path)
        if mod is None:
            results[name] = f"not loaded ({err})"
            if name in REQUIRED_MODULES:
                fatal.append(f"{name}: {err}")
            continue

        reg = getattr(mod, "register", None)
        if not callable(reg):
            results[name] = "loaded but missing register()"
            if name in REQUIRED_MODULES:
                fatal.append(f"{name}: missing register()")
            continue

        try:
            reg(bot, tree)
            results[name] = "registered"
        except Exception as e:
            logger.exception("commands module register failed: %s", mod_path)
            results[name] = f"register failed: {e}"
            if name in REQUIRED_MODULES:
                fatal.append(f"{name}: register failed")

    # Log a compact summary (deterministic order)
    summary = ", ".join([f"{k}={results.get(k, 'unknown')}" for k in MODULES])
    logger.info("discord commands registration summary: %s", summary)

    # Fail-closed: if required modules are missing/broken, do not continue silently.
    if fatal:
        msg = "Required command modules failed to load/register: " + "; ".join(fatal)
        logger.error(msg)
        raise RuntimeError(msg)
