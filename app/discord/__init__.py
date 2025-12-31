"""
Discord integration package.

Design goals:
- Keep app.discord.bot as the stable entrypoint (DashboardBot + run_bot).
- Allow commands to be split into app.discord.commands.* without changing external imports.
"""

from .bot import DashboardBot, bot, run_bot  # re-export for convenience

__all__ = [
    "DashboardBot",
    "bot",
    "run_bot",
]
