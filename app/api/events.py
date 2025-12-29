from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select
from ..database import get_session
from ..models.event import Event

router = APIRouter(prefix="/events", tags=["events"])

@router.post("/", response_model=Event)
def create_event(event: Event) -> Event:
    with get_session() as session:
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

@router.get("/", response_model=list[Event])
def list_events() -> list[Event]:
    with get_session() as session:
        return list(session.exec(select(Event)).all())
