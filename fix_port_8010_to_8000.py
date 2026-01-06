from __future__ import annotations

import os
from pathlib import Path

OLD = "8000"
NEW = "8000"

ROOT = Path(__file__).resolve().parent

def is_text_file(path: Path) -> bool:
    """
    Heuristic: try reading as UTF-8.
    If it fails, treat as binary and skip.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            f.read()
        return True
    except Exception:
        return False


def main() -> None:
    changed_files = []

    for root, dirs, files in os.walk(ROOT):
        # Skip virtualenvs and git internals defensively
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__"}]

        for name in files:
            path = Path(root) / name

            # Skip obvious binary extensions
            if path.suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".gif", ".ico",
                ".pdf", ".zip", ".exe", ".dll", ".so", ".pyd"
            }:
                continue

            if not is_text_file(path):
                continue

            try:
                original = path.read_text(encoding="utf-8")
            except Exception:
                continue

            if OLD not in original:
                continue

            updated = original.replace(OLD, NEW)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                changed_files.append(path.relative_to(ROOT))

    if not changed_files:
        print("No occurrences of 8000 found.")
        return

    print("Updated files:")
    for p in changed_files:
        print(f"  - {p}")

    print(f"\nDone. Replaced {OLD} → {NEW} in {len(changed_files)} file(s).")


if __name__ == "__main__":
    main()
