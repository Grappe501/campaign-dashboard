from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    import discord
    from discord import app_commands

logger = logging.getLogger(__name__)

# Single registry of command modules for the bot.
# Add new modules here as we expand capabilities.
MODULES: Sequence[str] = (
    "core",        # /ping, /config
    "onboarding",  # /start, /whoami
    "impact",      # /log, /reach, /my_next
    "approvals",   # /request_team_access + admin review flows
    "power5",      # power-of-5 commands
    "training",    # trainings + completion
    "external",    # census/bls proxy lookups
    "access",      # /sync_me role sync
)

__all__ = ["register_all", "MODULES"]


def _import_module(mod_path: str):
    """
    Import a command module safely.

    We treat ModuleNotFoundError as non-fatal (module not present yet).
    Any other exception indicates the module exists but failed during import.
    """
    try:
        return importlib.import_module(mod_path)
    except ModuleNotFoundError as e:
        logger.debug("commands module missing (%s): %s", mod_path, e)
        return None
    except Exception as e:
        logger.exception("commands module import failed (%s): %s", mod_path, e)
        return None


def register_all(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Register all command modules with the shared CommandTree.

    Pattern:
      - bot.py stays small/stable (entrypoint doesn't change)
      - commands are split into focused modules under discord/commands/
      - avoids circular imports by passing (bot, tree) explicitly

    Each module must expose:
        def register(bot, tree) -> None
    """
    pkg = __name__  # e.g. "app.discord.commands" (preferred) or "discord.commands"

    for name in MODULES:
        mod_path = f"{pkg}.{name}"
        mod = _import_module(mod_path)
        if mod is None:
            continue

        reg = getattr(mod, "register", None)
        if not callable(reg):
            logger.debug("commands module has no register(): %s", mod_path)
            continue

        try:
            reg(bot, tree)
        except Exception:
            logger.exception("commands module register failed: %s", mod_path)
