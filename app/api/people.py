from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select
from ..database import get_session
from ..models.person import Person
from ..services.impact_engine import compute_impact

router = APIRouter(prefix="/people", tags=["people"])

@router.post("/", response_model=Person)
def create_person(person: Person) -> Person:
    with get_session() as session:
        session.add(person)
        session.commit()
        session.refresh(person)
        return person

@router.get("/", response_model=list[Person])
def list_people() -> list[Person]:
    with get_session() as session:
        return list(session.exec(select(Person)).all())

@router.get("/{person_id}", response_model=Person)
def get_person(person_id: int) -> Person:
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        return p

@router.get("/{person_id}/impact")
def get_person_impact(person_id: int):
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        summary = compute_impact(session, person_id)
        return summary.__dict__
