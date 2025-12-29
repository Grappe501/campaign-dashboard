from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, HTTPException
from sqlmodel import select
from ..database import get_session
from ..models.voter import VoterContact, VOTER_STEPS

router = APIRouter(prefix="/voters", tags=["voters"])

@router.post("/", response_model=VoterContact)
def create_voter(voter: VoterContact) -> VoterContact:
    if voter.step not in VOTER_STEPS:
        raise HTTPException(status_code=400, detail=f"Invalid step. Allowed: {VOTER_STEPS}")
    with get_session() as session:
        session.add(voter)
        session.commit()
        session.refresh(voter)
        return voter

@router.get("/", response_model=list[VoterContact])
def list_voters(owner_person_id: int | None = None) -> list[VoterContact]:
    with get_session() as session:
        q = select(VoterContact)
        if owner_person_id is not None:
            q = q.where(VoterContact.owner_person_id == owner_person_id)
        return list(session.exec(q).all())

@router.patch("/{voter_id}", response_model=VoterContact)
def update_step(voter_id: int, step: str) -> VoterContact:
    if step not in VOTER_STEPS:
        raise HTTPException(status_code=400, detail=f"Invalid step. Allowed: {VOTER_STEPS}")
    with get_session() as session:
        v = session.get(VoterContact, voter_id)
        if not v:
            raise HTTPException(status_code=404, detail="Voter contact not found")
        v.step = step
        v.updated_at = datetime.utcnow()
        session.add(v)
        session.commit()
        session.refresh(v)
        return v
