from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..database import get_session
from ..models.voter import VoterContact, VOTER_STEPS

router = APIRouter(prefix="/voters", tags=["voters"])


# -------------------------
# Schemas (API-safe)
# -------------------------


class VoterContactCreate(BaseModel):
    owner_person_id: int
    name: Optional[str] = None
    county: Optional[str] = None
    step: Optional[str] = None
    notes: Optional[str] = None


class VoterContactPatch(BaseModel):
    step: Optional[str] = None
    name: Optional[str] = None
    county: Optional[str] = None
    notes: Optional[str] = None


def _bad_step_message() -> str:
    return f"Invalid step. Allowed: {', '.join(VOTER_STEPS)}"


# -------------------------
# Routes
# -------------------------


@router.post("/", response_model=VoterContact)
def create_voter(payload: VoterContactCreate) -> VoterContact:
    try:
        step = VoterContact.normalize_step(payload.step)
    except Exception:
        raise HTTPException(status_code=400, detail=_bad_step_message())

    voter = VoterContact(
        owner_person_id=payload.owner_person_id,
        name=(payload.name.strip() if payload.name else None),
        county=(payload.county.strip() if payload.county else None),
        step=step,
        notes=(payload.notes.strip() if payload.notes else None),
    )

    with get_session() as session:
        session.add(voter)
        session.commit()
        session.refresh(voter)
        return voter


@router.get("/", response_model=List[VoterContact])
def list_voters(
    owner_person_id: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[VoterContact]:
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))

    with get_session() as session:
        q = select(VoterContact)
        if owner_person_id is not None:
            q = q.where(VoterContact.owner_person_id == owner_person_id)

        q = q.order_by(VoterContact.updated_at.desc()).offset(offset).limit(limit)
        return list(session.exec(q).all())


@router.get("/{voter_id}", response_model=VoterContact)
def get_voter(voter_id: int) -> VoterContact:
    with get_session() as session:
        v = session.get(VoterContact, voter_id)
        if not v:
            raise HTTPException(status_code=404, detail="Voter contact not found")
        return v


@router.patch("/{voter_id}", response_model=VoterContact)
def update_voter(voter_id: int, payload: VoterContactPatch) -> VoterContact:
    with get_session() as session:
        v = session.get(VoterContact, voter_id)
        if not v:
            raise HTTPException(status_code=404, detail="Voter contact not found")

        # Step validation uses model helper (and model event will re-validate too)
        if payload.step is not None:
            try:
                v.step = VoterContact.normalize_step(payload.step)
            except Exception:
                raise HTTPException(status_code=400, detail=_bad_step_message())

        if payload.name is not None:
            v.name = payload.name.strip() or None
        if payload.county is not None:
            v.county = payload.county.strip() or None
        if payload.notes is not None:
            v.notes = payload.notes.strip() or None

        session.add(v)
        session.commit()
        session.refresh(v)
        return v


@router.get("/steps/all", response_model=Dict[str, Any])
def list_steps() -> Dict[str, Any]:
    return {"steps": list(VOTER_STEPS)}
