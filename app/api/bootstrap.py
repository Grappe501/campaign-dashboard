from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any
from uuid import uuid4

from fastapi import APIRouter
from sqlmodel import select

from ..database import get_session
from ..models.person import Person
from ..models.power_team import PowerTeam
from ..models.impact_rule import ImpactRule
from ..api.impact import DEFAULT_RULES

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


def _make_tracking_number() -> str:
    """
    Person.tracking_number is required + unique.
    Generate a short, readable-ish token for local beta.
    """
    return "P-" + uuid4().hex[:10].upper()


@router.post("/rules")
def bootstrap_rules() -> Dict[str, Any]:
    """
    Idempotently create default impact rules if missing.
    """
    with get_session() as session:
        existing = {r.action_type for r in session.exec(select(ImpactRule)).all()}
        created = 0
        for k, v in DEFAULT_RULES.items():
            if k in existing:
                continue
            session.add(ImpactRule(action_type=k, reach_per_unit=v, notes="bootstrap default"))
            created += 1
        session.commit()
        return {"created": created, "defaults": DEFAULT_RULES}


@router.post("/power5_team")
def bootstrap_power5_team(
    leader_name: str = "Test Leader",
    leader_email: Optional[str] = None,
    leader_phone: Optional[str] = None,
    leader_discord_user_id: Optional[str] = None,
    team_name: str = "Power of 5 (Beta)",
) -> Dict[str, Any]:
    """
    Creates (or reuses) a leader Person and creates a PowerTeam.
    Designed for local/beta setup and Discord /setup.

    Reuse priority: email -> discord_user_id -> phone (if provided).
    """
    with get_session() as session:
        leader: Optional[Person] = None

        # 1) find leader if possible (reuse)
        if leader_email:
            leader = session.exec(select(Person).where(Person.email == leader_email)).first()
        elif leader_discord_user_id:
            leader = session.exec(select(Person).where(Person.discord_user_id == leader_discord_user_id)).first()
        elif leader_phone:
            leader = session.exec(select(Person).where(Person.phone == leader_phone)).first()

        # 2) create leader if missing
        if not leader:
            # ensure uniqueness for tracking_number; retry on collision (very unlikely)
            for _ in range(5):
                tracking_number = _make_tracking_number()
                exists = session.exec(select(Person).where(Person.tracking_number == tracking_number)).first()
                if not exists:
                    break
            else:
                # fall back to a longer token if something is very wrong
                tracking_number = "P-" + uuid4().hex.upper()

            leader = Person(
                tracking_number=tracking_number,
                name=leader_name,
                email=leader_email,
                phone=leader_phone,
                discord_user_id=leader_discord_user_id,
                stage="active",
                created_at=datetime.utcnow(),
            )
            session.add(leader)
            session.commit()
            session.refresh(leader)

        # 3) create team
        team = PowerTeam(leader_person_id=leader.id, name=team_name)
        session.add(team)
        session.commit()
        session.refresh(team)

        return {
            "leader_person_id": leader.id,
            "leader_tracking_number": leader.tracking_number,
            "leader_name": leader.name,
            "power_team_id": team.id,
            "power_team_name": team.name,
        }
