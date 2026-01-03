from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlmodel import Session, select

from app.database import get_db
from app.models.person import Person
from app.models.training_completion import TrainingCompletion
from app.models.training_module import TrainingModule

router = APIRouter(prefix="/training", tags=["training"])


class TrainingCompleteRequest(BaseModel):
    person_id: int = PydField(..., ge=1)
    module_id: Optional[int] = PydField(None, ge=1)
    module_slug: Optional[str] = None
    note: Optional[str] = None


def _clean_slug(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    out = s.strip().lower()
    return out or None


@router.get("/modules")
def list_training_modules(
    *,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """
    Return available training modules for volunteers.

    Used by Discord bot command:
      /training modules
    """
    modules: List[TrainingModule] = db.exec(
        select(TrainingModule)
        .where(TrainingModule.is_active == True)  # noqa: E712
        .order_by(TrainingModule.sort_order, TrainingModule.id)
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "id": m.id,
                "slug": m.slug,
                "title": m.title,
                "description": m.description,
                "estimated_minutes": m.estimated_minutes,
            }
            for m in modules
        ]
    }


@router.post("/complete")
def complete_training_module(
    *,
    db: Session = Depends(get_db),
    payload: TrainingCompleteRequest,
) -> Dict[str, Any]:
    """
    Mark a training module as completed by a person.

    Expected request body:
      {
        "person_id": 123,
        "module_id": 5,         # OR module_slug
        "module_slug": "sop-101",
        "note": "optional"
      }

    Used by Discord bot command:
      /training complete
    """
    person_id = payload.person_id
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    module: Optional[TrainingModule] = None

    if payload.module_id is not None:
        module = db.get(TrainingModule, payload.module_id)

    if module is None:
        slug = _clean_slug(payload.module_slug)
        if slug:
            module = db.exec(select(TrainingModule).where(TrainingModule.slug == slug)).first()

    if not module:
        raise HTTPException(status_code=404, detail="Training module not found")

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
        note=payload.note,
    )

    # Optional validation hook (safe no-op if you don't call it elsewhere)
    try:
        completion.validate()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(completion)
    db.commit()
    db.refresh(completion)

    return {
        "status": "completed",
        "person_id": person_id,
        "module_id": module.id,
        "module_slug": module.slug,
        "completed_at": completion.completed_at.isoformat(),
    }
