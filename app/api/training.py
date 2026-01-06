from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_db
from app.models.person import Person
from app.models.training_completion import TrainingCompletion
from app.models.training_module import TrainingModule

router = APIRouter(prefix="/training", tags=["training"])


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class TrainingCompleteRequest(BaseModel):
    person_id: int = PydField(..., ge=1)
    module_id: Optional[int] = PydField(None, ge=1)
    module_slug: Optional[str] = None
    note: Optional[str] = None


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _clean_slug(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    out = s.strip().lower()
    return out or None


def _clean_note(note: Optional[str], max_len: int = 500) -> Optional[str]:
    """
    TrainingCompletion.note is max_length=500 in the model.
    Keep API aligned so we never exceed DB/model constraints.
    """
    if note is None:
        return None
    s = str(note).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _ensure_person_exists(db: Session, person_id: int) -> None:
    if person_id < 1:
        raise HTTPException(status_code=400, detail="person_id must be >= 1")
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _serialize_module(m: TrainingModule) -> Dict[str, Any]:
    return {
        "id": m.id,
        "slug": m.slug,
        "title": m.title,
        "description": m.description,
        "estimated_minutes": m.estimated_minutes,
        "is_active": bool(getattr(m, "is_active", True)),
        "sort_order": getattr(m, "sort_order", None),
    }


def _apply_module_search(
    query: Any,
    *,
    text_query: Optional[str],
    active_only: bool,
) -> Any:
    """
    Apply simple search filters.
    - active_only gates on TrainingModule.is_active
    - text_query searches slug/title/description (ILIKE best-effort)
    """
    if active_only:
        query = query.where(TrainingModule.is_active == True)  # noqa: E712

    if text_query:
        raw = text_query.strip()
        if raw:
            needle = f"%{raw}%"
            query = query.where(
                (TrainingModule.slug.ilike(needle))
                | (TrainingModule.title.ilike(needle))
                | (TrainingModule.description.ilike(needle))
            )

    return query


def _count_modules(
    db: Session,
    *,
    text_query: Optional[str],
    include_inactive: bool,
) -> int:
    """
    Return total matching module count for paging UI.

    Important: count from the *same filtered dataset* as /modules returns,
    so the Discord UI can reliably disable Next/Prev.
    """
    base = select(TrainingModule.id)
    base = _apply_module_search(base, text_query=text_query, active_only=not include_inactive)
    subq = base.subquery()

    q = select(func.count()).select_from(subq)

    try:
        n = db.exec(q).one()
    except Exception:
        return 0

    # SQLModel/SQLAlchemy can return int OR a 1-tuple depending on backend/driver.
    try:
        if isinstance(n, tuple):
            return int(n[0] or 0)
        return int(n or 0)
    except Exception:
        return 0


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.get("/modules")
def list_training_modules(
    *,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    q: Optional[str] = Query(None, description="Optional search across slug/title/description"),
    include_inactive: bool = Query(False, description="If true, include inactive modules"),
) -> Dict[str, Any]:
    """
    Return training modules (paged + searchable).

    Used by Discord bot:
      - /trainings (interactive browser)
      - Autocomplete (q + limit 25)
      - UI paging (offset)

    Response includes:
      - items
      - limit / offset
      - total_count
      - has_more / next_offset (simple UX helpers)
    """
    base = select(TrainingModule)
    base = _apply_module_search(base, text_query=q, active_only=not include_inactive)

    total_count = _count_modules(db, text_query=q, include_inactive=include_inactive)

    query = base.order_by(TrainingModule.sort_order, TrainingModule.id).offset(offset).limit(limit)
    modules: List[TrainingModule] = db.exec(query).all()

    # Paging helpers for UI (Discord view can use total_count OR has_more)
    shown = len(modules)
    next_offset = offset + shown
    has_more = next_offset < total_count

    return {
        "limit": limit,
        "offset": offset,
        "total_count": total_count,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "items": [_serialize_module(m) for m in modules],
    }


@router.get("/modules/{module_id}")
def get_training_module(
    *,
    db: Session = Depends(get_db),
    module_id: int,
) -> Dict[str, Any]:
    """
    Fetch one training module by id (useful for UI “View details” flows).
    """
    if module_id < 1:
        raise HTTPException(status_code=400, detail="module_id must be >= 1")

    m = db.get(TrainingModule, module_id)
    if not m:
        raise HTTPException(status_code=404, detail="Training module not found")

    return {"item": _serialize_module(m)}


@router.get("/completions")
def list_training_completions(
    *,
    db: Session = Depends(get_db),
    person_id: int = Query(..., ge=1),
    limit: int = Query(200, ge=1, le=500),
) -> Dict[str, Any]:
    """
    List completed training modules for a person.

    Intended for bot/UI:
      - show what a volunteer has finished
      - audit completion timestamps
    """
    _ensure_person_exists(db, person_id)

    comps: List[TrainingCompletion] = db.exec(
        select(TrainingCompletion)
        .where(TrainingCompletion.person_id == person_id)
        .order_by(TrainingCompletion.completed_at.desc(), TrainingCompletion.id.desc())
        .limit(limit)
    ).all()

    module_ids = sorted({int(c.module_id) for c in comps if c.module_id is not None})
    modules_by_id: Dict[int, TrainingModule] = {}
    if module_ids:
        modules = db.exec(select(TrainingModule).where(TrainingModule.id.in_(module_ids))).all()
        modules_by_id = {int(m.id): m for m in modules if m.id is not None}

    items: List[Dict[str, Any]] = []
    for c in comps:
        mid = int(c.module_id) if c.module_id is not None else 0
        m = modules_by_id.get(mid)
        items.append(
            {
                "id": c.id,
                "person_id": c.person_id,
                "module_id": c.module_id,
                "module_slug": getattr(m, "slug", None) if m else None,
                "module_title": getattr(m, "title", None) if m else None,
                "completed_at": c.completed_at.isoformat(),
                "note": c.note,
            }
        )

    return {"items": items}


@router.get("/progress")
def training_progress(
    *,
    db: Session = Depends(get_db),
    person_id: int = Query(..., ge=1),
    limit: int = Query(200, ge=1, le=500),
    include_inactive: bool = Query(False),
) -> Dict[str, Any]:
    """
    Return modules plus completion status for a person.

    Used by Discord bot:
      - /my_trainings
      - UI overlay of completion checkmarks
    """
    _ensure_person_exists(db, person_id)

    modules_query = select(TrainingModule).order_by(TrainingModule.sort_order, TrainingModule.id).limit(limit)
    if not include_inactive:
        modules_query = modules_query.where(TrainingModule.is_active == True)  # noqa: E712

    modules: List[TrainingModule] = db.exec(modules_query).all()

    completions: List[TrainingCompletion] = db.exec(
        select(TrainingCompletion).where(TrainingCompletion.person_id == person_id)
    ).all()

    completed_by_module_id: Dict[int, TrainingCompletion] = {}
    for c in completions:
        mid = int(c.module_id)
        prev = completed_by_module_id.get(mid)
        if prev is None or c.completed_at > prev.completed_at:
            completed_by_module_id[mid] = c

    items: List[Dict[str, Any]] = []
    for m in modules:
        mid = int(m.id) if m.id is not None else 0
        c = completed_by_module_id.get(mid)
        items.append(
            {
                "id": m.id,
                "slug": m.slug,
                "title": m.title,
                "description": m.description,
                "estimated_minutes": m.estimated_minutes,
                "completed": c is not None,
                "completed_at": c.completed_at.isoformat() if c else None,
            }
        )

    completed_count = sum(1 for it in items if it.get("completed"))
    return {
        "person_id": person_id,
        "completed_count": completed_count,
        "total_count": len(items),
        "items": items,
    }


@router.post("/complete")
def complete_training_module(
    *,
    db: Session = Depends(get_db),
    payload: TrainingCompleteRequest,
) -> Dict[str, Any]:
    """
    Mark a training module as completed by a person.

    Used by Discord bot:
      - /training_complete
      - UI “Mark Complete” button + modal note
    """
    person_id = int(payload.person_id)
    _ensure_person_exists(db, person_id)

    module: Optional[TrainingModule] = None

    if payload.module_id is not None:
        module = db.get(TrainingModule, int(payload.module_id))

    if module is None:
        slug = _clean_slug(payload.module_slug)
        if slug:
            module = db.exec(select(TrainingModule).where(TrainingModule.slug == slug)).first()

    if not module:
        raise HTTPException(status_code=404, detail="Training module not found")

    # Fast path: if it already exists, return it.
    existing = db.exec(
        select(TrainingCompletion).where(
            TrainingCompletion.person_id == person_id,
            TrainingCompletion.module_id == module.id,
        )
    ).first()

    if existing:
        return {
            "status": "already_completed",
            "person_id": person_id,
            "module_id": module.id,
            "module_slug": module.slug,
            "completed_at": existing.completed_at.isoformat(),
        }

    completion = TrainingCompletion(
        person_id=person_id,
        module_id=int(module.id),
        completed_at=datetime.utcnow(),
        note=_clean_note(payload.note),
    )

    try:
        completion.validate()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(completion)

    try:
        db.commit()
    except IntegrityError:
        # Race-safe: if the unique constraint tripped, someone completed already.
        db.rollback()
        existing2 = db.exec(
            select(TrainingCompletion).where(
                TrainingCompletion.person_id == person_id,
                TrainingCompletion.module_id == module.id,
            )
        ).first()
        if existing2:
            return {
                "status": "already_completed",
                "person_id": person_id,
                "module_id": module.id,
                "module_slug": module.slug,
                "completed_at": existing2.completed_at.isoformat(),
            }
        # If we somehow can't find it, surface a generic conflict
        raise HTTPException(status_code=409, detail="Training completion already exists")

    db.refresh(completion)

    return {
        "status": "completed",
        "person_id": person_id,
        "module_id": module.id,
        "module_slug": module.slug,
        "completed_at": completion.completed_at.isoformat(),
    }
