"""
API entrypoint.

Operator notes:
- This file should remain extremely small and boring.
- Runtime configuration is pulled from environment variables inside app.main.run()
  (we avoid importing app.config here due to backend/bot config-name collision).
- If this file crashes, the error should be immediately obvious to the operator.
"""

import logging
import sys

from app.main import run


def main() -> None:
    try:
        run()
    except Exception:
        logging.basicConfig(level=logging.ERROR)
        logging.exception("API failed to start.")
        print("\n❌ API failed to start.")
        print("   See error above. Most common causes:")
        print("   - Database path/URL invalid (DATABASE_URL or DB_PATH)")
        print("   - Port already in use (PORT)")
        print("   - Missing dependencies / broken venv\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
