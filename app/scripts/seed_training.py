from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select

from app.database import init_db, session_scope
from app.models.training_module import TrainingModule


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------
# Seed data (starter SOP / training modules)
# ---------------------------------------------------------------------

TRAINING_MODULES: List[Dict[str, Optional[str | int | bool]]] = [
    {
        "slug": "orientation",
        "title": "Volunteer Orientation",
        "description": "Welcome to the campaign. Learn how we organize, communicate, and win together.",
        "sort_order": 10,
        "estimated_minutes": 10,
        "is_active": True,
    },
    {
        "slug": "power-of-5",
        "title": "Power of 5 Basics",
        "description": "How the Power of 5 system works and how to build your first team.",
        "sort_order": 20,
        "estimated_minutes": 15,
        "is_active": True,
    },
    {
        "slug": "call-time-sop",
        "title": "Call Time SOP",
        "description": "Standard operating procedures for voter and supporter call time.",
        "sort_order": 30,
        "estimated_minutes": 20,
        "is_active": True,
    },
    {
        "slug": "discord-usage",
        "title": "Using Discord for Campaign Work",
        "description": "How we use Discord for coordination, reporting, and support.",
        "sort_order": 40,
        "estimated_minutes": 10,
        "is_active": True,
    },
]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def upsert_training_module(session, row: Dict[str, Optional[str | int | bool]]) -> TrainingModule:
    """
    Upsert by slug (stable identifier).

    - Updates title/description/sort_order/estimated_minutes/is_active
    - Preserves created_at
    """
    slug = str(row["slug"]).strip().lower()

    existing: Optional[TrainingModule] = session.exec(
        select(TrainingModule).where(TrainingModule.slug == slug)
    ).first()

    if existing:
        existing.title = str(row["title"])
        existing.description = str(row.get("description") or "")
        existing.sort_order = int(row.get("sort_order") or existing.sort_order)
        existing.estimated_minutes = row.get("estimated_minutes")
        existing.is_active = bool(row.get("is_active", True))
        existing.updated_at = utcnow()
        session.add(existing)
        return existing

    module = TrainingModule(
        slug=slug,
        title=str(row["title"]),
        description=str(row.get("description") or ""),
        sort_order=int(row.get("sort_order") or 100),
        estimated_minutes=row.get("estimated_minutes"),
        is_active=bool(row.get("is_active", True)),
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    # Validate once on insert
    module.validate()

    session.add(module)
    return module


# ---------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------

def main() -> None:
    # Ensure tables exist (local dev)
    init_db()

    with session_scope() as session:
        for row in TRAINING_MODULES:
            upsert_training_module(session, row)

        total = session.exec(select(TrainingModule)).all()
        active = session.exec(
            select(TrainingModule).where(TrainingModule.is_active == True)  # noqa: E712
        ).all()

    print(f"Seeded/updated training modules: {len(total)} (active: {len(active)})")


if __name__ == "__main__":
    main()
