from __future__ import annotations

import importlib
import logging
import os
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    import discord
    from discord import app_commands

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Command module registry (Operator Readiness)
# ---------------------------------------------------------------------
# We maintain a canonical list of "capabilities" here (stable names).
# Under the hood, those names map to actual python modules on disk.
#
# Why:
# - Operators toggle modules via DISCORD_COMMANDS_ALLOW/DENY
# - We want those toggles to be stable even if we rename files later
# - We must not silently "think" something loaded when it didn't
#
# Hardening goals:
# - Deterministic module ordering
# - Clear logging of what loaded/registered/failed
# - Fail-closed for required modules (so the bot doesn't come up "half working")
MODULES: Sequence[str] = (
    "core",        # /ping, /config
    "onboarding",  # /start, /whoami
    "impact",      # /log, /reach, /my_next
    "approvals",   # /request_team_access + admin review flows
    "power5",      # power-of-5 commands (optional)

    # Optional capabilities (canonical names)
    "trainings",   # trainings + completion (optional)  -> training.py in this build
    "external",    # census/bls proxy lookups (optional)
    "role_sync",   # /sync_me role sync (optional)      -> _me.py in this build
)

# Modules that must be present and successfully register for the bot to be considered healthy.
REQUIRED_MODULES: Sequence[str] = (
    "core",
    "onboarding",
    "impact",
    "approvals",
)

# Canonical name -> module filename on disk (without package prefix)
# Operators may toggle either the canonical name (trainings) or the file name (training).
_MODULE_ALIASES: Dict[str, str] = {
    "trainings": "training",
    "role_sync": "_me",
}

__all__ = ["register_all", "resolved_command_modules", "MODULES", "REQUIRED_MODULES"]


def _env_list(name: str) -> Optional[Sequence[str]]:
    """
    Optional allow/deny lists for module registration.
    Comma-separated module names.

    Examples:
      DISCORD_COMMANDS_ALLOW=core,onboarding,impact,approvals
      DISCORD_COMMANDS_DENY=external,trainings

    Notes:
    - Case-insensitive
    - Operators may specify canonical names (trainings) or file module names (training).
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    parts = [p.strip().lower() for p in raw.split(",")]
    return [p for p in parts if p]


def _normalize_token(s: str) -> str:
    return (s or "").strip().lower()


def _candidate_module_names(canonical: str) -> List[str]:
    """
    Import candidates in deterministic order:
    1) canonical capability name (e.g., "trainings")
    2) alias target filename (e.g., "training") if configured and different
    """
    name = _normalize_token(canonical)
    alias = _MODULE_ALIASES.get(name)
    cands = [name]
    if alias and alias != name:
        cands.append(alias)
    return cands


def _should_register(canonical: str) -> bool:
    """
    Apply allow/deny lists if present.
    Defaults to register all MODULES.

    Precedence:
    - allow list (if present) wins over deny list

    Operator-friendly behavior:
    - allow/deny entries may be canonical names or alias module filenames
    """
    allow = _env_list("DISCORD_COMMANDS_ALLOW")
    deny = _env_list("DISCORD_COMMANDS_DENY")

    name = _normalize_token(canonical)
    aliases = set(_candidate_module_names(name))  # canonical + alias filename(s)

    if allow is not None:
        allowed = set(_normalize_token(x) for x in allow)
        # if any candidate name is allowed, register
        return bool(aliases & allowed)

    if deny is not None:
        denied = set(_normalize_token(x) for x in deny)
        # if any candidate name is denied, do not register
        return not bool(aliases & denied)

    return True


def resolved_command_modules() -> List[str]:
    """
    Operator-friendly helper: which MODULES would be attempted, after allow/deny rules.
    Does not import any modules.
    """
    return [m for m in MODULES if _should_register(m)]


def _is_missing_target_module(mod_path: str, exc: ModuleNotFoundError) -> bool:
    """
    Determine whether a ModuleNotFoundError indicates the module itself is missing,
    versus a dependency inside the module.

    Rule:
    - If exc.name matches the mod_path or a direct submodule of it, treat as "missing module"
    - Otherwise, treat as dependency/import error (operator action required)
    """
    missing_name = getattr(exc, "name", "") or ""
    if not missing_name:
        return False
    return missing_name == mod_path or missing_name.startswith(mod_path + ".")


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
        if _is_missing_target_module(mod_path, e):
            return None, f"missing module: {getattr(e, 'name', '') or mod_path}"
        dep = getattr(e, "name", "") or str(e)
        return None, f"import error (dependency missing): {dep}"
    except Exception as e:
        return None, f"import error: {e}"


def register_all(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Register all command modules with the shared CommandTree.

    Each module must expose:
        def register(bot, tree) -> None

    Hardening:
      - deterministic registration report in logs
      - fail-closed if any REQUIRED_MODULE fails to import/register
      - alias support (canonical capability -> on-disk module filename)
    """
    pkg = __name__  # e.g. "app.discord.commands"
    results: Dict[str, str] = {}
    fatal: List[str] = []

    allow = _env_list("DISCORD_COMMANDS_ALLOW")
    deny = _env_list("DISCORD_COMMANDS_DENY")
    if allow is not None:
        logger.info("discord commands allow-list enabled: %s", ",".join(allow))
    elif deny is not None:
        logger.info("discord commands deny-list enabled: %s", ",".join(deny))

    for canonical in MODULES:
        if not _should_register(canonical):
            results[canonical] = "skipped (allow/deny)"
            continue

        loaded_mod: Optional[object] = None
        loaded_path: Optional[str] = None
        last_err: Optional[str] = None

        # Try canonical name first, then alias filename (if configured)
        for candidate in _candidate_module_names(canonical):
            mod_path = f"{pkg}.{candidate}"
            mod, err = _import_module(mod_path)
            if mod is not None:
                loaded_mod = mod
                loaded_path = mod_path
                last_err = None
                break
            last_err = err

        if loaded_mod is None:
            results[canonical] = f"not loaded ({last_err})"
            if canonical in REQUIRED_MODULES:
                fatal.append(f"{canonical}: {last_err}")
            continue

        reg = getattr(loaded_mod, "register", None)
        if not callable(reg):
            results[canonical] = f"loaded({loaded_path}) but missing register()"
            if canonical in REQUIRED_MODULES:
                fatal.append(f"{canonical}: missing register()")
            continue

        try:
            reg(bot, tree)
            results[canonical] = f"registered({loaded_path})"
        except Exception as e:
            logger.exception("commands module register failed: %s", loaded_path or canonical)
            results[canonical] = f"register failed({loaded_path}): {e}"
            if canonical in REQUIRED_MODULES:
                fatal.append(f"{canonical}: register failed")

    # Log a compact summary (deterministic order)
    summary = ", ".join([f"{k}={results.get(k, 'unknown')}" for k in MODULES])
    logger.info("discord commands registration summary: %s", summary)

    # Fail-closed: if required modules are missing/broken, do not continue silently.
    if fatal:
        msg = "Required command modules failed to load/register: " + "; ".join(fatal)
        logger.error(msg)
        raise RuntimeError(msg)
