from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select
from ..database import get_session
from ..models.power_team import PowerTeam, PowerTeamMember

router = APIRouter(prefix="/teams", tags=["power_teams"])

@router.post("/", response_model=PowerTeam)
def create_team(team: PowerTeam) -> PowerTeam:
    with get_session() as session:
        session.add(team)
        session.commit()
        session.refresh(team)
        return team

@router.get("/", response_model=list[PowerTeam])
def list_teams() -> list[PowerTeam]:
    with get_session() as session:
        return list(session.exec(select(PowerTeam)).all())

@router.post("/{team_id}/members", response_model=PowerTeamMember)
def add_member(team_id: int, member: PowerTeamMember) -> PowerTeamMember:
    if member.power_team_id != team_id:
        raise HTTPException(status_code=400, detail="power_team_id mismatch")
    with get_session() as session:
        session.add(member)
        session.commit()
        session.refresh(member)
        return member

@router.get("/{team_id}/members", response_model=list[PowerTeamMember])
def list_members(team_id: int) -> list[PowerTeamMember]:
    with get_session() as session:
        q = select(PowerTeamMember).where(PowerTeamMember.power_team_id == team_id)
        return list(session.exec(q).all())
