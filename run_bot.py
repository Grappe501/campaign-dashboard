"""
Bot entrypoint.

Operator notes:
- This file should remain extremely small and boring.
- All configuration validation happens inside run_bot().
- If this file crashes, the error should be immediately obvious to the operator.
"""

import logging
import sys

from app.discord.bot import run_bot


def main() -> None:
    try:
        run_bot()
    except Exception as exc:
        # Fail loud and early with a clear signal for operators.
        logging.basicConfig(level=logging.ERROR)
        logging.exception("Discord bot failed to start.")
        print("\n❌ Discord bot failed to start.")
        print("   See error above. Most common causes:")
        print("   - DISCORD_BOT_TOKEN missing or not loaded into the environment")
        print("   - Invalid DASHBOARD_API_BASE")
        print("   - DISCORD_SYNC_GUILD_ONLY=true without DISCORD_GUILD_ID\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
