from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlmodel import select

from ..database import get_session
from ..models.person import Person
from ..models.power_team import PowerTeam, PowerTeamMember

router = APIRouter(prefix="/teams", tags=["power_teams"])


# -----------------------------
# Schemas
# -----------------------------


class PowerTeamCreate(BaseModel):
    """
    Schema-based create so clients can't accidentally pass DB-only fields.

    Note: current PowerTeam model requires leader_person_id and does NOT include `description`.
    """
    leader_person_id: int = PydField(..., ge=1)
    name: str = PydField(default="Power of 5", min_length=1, max_length=120)
    min_goal_size: int = PydField(default=5, ge=1, le=500)


class PowerTeamMemberCreate(BaseModel):
    """
    Backward-compatible membership add.

    Accepts:
      - person_id (preferred)
      - discord_user_id (optional convenience)

    Note: we do NOT gate this by team_access here because:
      - some teams are onboarding/friendly
      - gating is handled by approvals + bot/Discord roles
    """
    power_team_id: Optional[int] = None
    person_id: Optional[int] = None
    discord_user_id: Optional[str] = None

    role: Optional[str] = None  # if model has it; safe if ignored by DB layer


# -----------------------------
# Helpers
# -----------------------------


def _find_person(session, *, person_id: Optional[int], discord_user_id: Optional[str]) -> Optional[Person]:
    if person_id is not None:
        return session.get(Person, person_id)
    if discord_user_id:
        return session.exec(select(Person).where(Person.discord_user_id == discord_user_id)).first()
    return None


# -----------------------------
# Routes
# -----------------------------


@router.post("/", response_model=PowerTeam)
def create_team(payload: PowerTeamCreate) -> PowerTeam:
    with get_session() as session:
        leader = session.get(Person, payload.leader_person_id)
        if not leader:
            raise HTTPException(status_code=404, detail="Leader person not found")

        team = PowerTeam(
            leader_person_id=payload.leader_person_id,
            name=payload.name.strip() or "Power of 5",
            min_goal_size=int(payload.min_goal_size),
        )
        session.add(team)
        session.commit()
        session.refresh(team)
        return team


@router.get("/", response_model=List[PowerTeam])
def list_teams(limit: int = 200, offset: int = 0) -> List[PowerTeam]:
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))

    with get_session() as session:
        q = select(PowerTeam).order_by(PowerTeam.created_at.desc()).offset(offset).limit(limit)
        return list(session.exec(q).all())


@router.get("/{team_id}", response_model=PowerTeam)
def get_team(team_id: int) -> PowerTeam:
    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        return team


@router.post("/{team_id}/members", response_model=PowerTeamMember)
def add_member(team_id: int, payload: PowerTeamMemberCreate) -> PowerTeamMember:
    """
    Add a member to a PowerTeam.

    Backward compatible:
    - If payload.power_team_id is present, it must match path param.
    - Can identify member by person_id or discord_user_id.
    """
    if payload.power_team_id is not None and payload.power_team_id != team_id:
        raise HTTPException(status_code=400, detail="power_team_id mismatch")

    if payload.person_id is None and not payload.discord_user_id:
        raise HTTPException(status_code=400, detail="Provide person_id or discord_user_id")

    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        person = _find_person(session, person_id=payload.person_id, discord_user_id=payload.discord_user_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        # Dedupe: don't add duplicates
        existing = session.exec(
            select(PowerTeamMember).where(
                PowerTeamMember.power_team_id == team_id,
                PowerTeamMember.person_id == person.id,
            )
        ).first()
        if existing:
            return existing

        member = PowerTeamMember(
            power_team_id=team_id,
            person_id=person.id,
        )

        # Optional role if model supports it
        if payload.role is not None and hasattr(member, "role"):
            try:
                setattr(member, "role", payload.role)
            except Exception:
                pass

        session.add(member)
        session.commit()
        session.refresh(member)
        return member


@router.get("/{team_id}/members", response_model=List[PowerTeamMember])
def list_members(team_id: int, limit: int = 200, offset: int = 0) -> List[PowerTeamMember]:
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))

    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        q = (
            select(PowerTeamMember)
            .where(PowerTeamMember.power_team_id == team_id)
            .order_by(PowerTeamMember.joined_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(q).all())
