from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..database import get_session
from ..models.power_team import PowerTeam
from ..models.power5_link import Power5Link
from ..models.power5_invite import Power5Invite

router = APIRouter(prefix="/power5", tags=["power5"])


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _compute_depth(session, team_id: int, parent_person_id: int) -> int:
    team = session.get(PowerTeam, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # If parent is the leader, child is depth 1
    if parent_person_id == team.leader_person_id:
        return 1

    # Otherwise, depth = parent's depth + 1 (must exist)
    q = select(Power5Link).where(
        Power5Link.power_team_id == team_id,
        Power5Link.child_person_id == parent_person_id,
    )
    parent_link = session.exec(q).first()
    if not parent_link:
        # keep simple and strict for now — avoids ambiguous trees
        raise HTTPException(status_code=400, detail="Parent is not in the Power5 tree for this team")
    return int(parent_link.depth) + 1


@router.post("/teams/{team_id}/links", response_model=Power5Link)
def upsert_link(team_id: int, link: Power5Link) -> Power5Link:
    if link.power_team_id != team_id:
        raise HTTPException(status_code=400, detail="power_team_id mismatch")

    with get_session() as session:
        # uniqueness: one child per team
        existing = session.exec(
            select(Power5Link).where(
                Power5Link.power_team_id == team_id,
                Power5Link.child_person_id == link.child_person_id,
            )
        ).first()

        depth = _compute_depth(session, team_id, link.parent_person_id)

        if existing:
            existing.parent_person_id = link.parent_person_id
            existing.status = link.status or existing.status
            existing.depth = depth
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        link.depth = depth
        session.add(link)
        session.commit()
        session.refresh(link)
        return link


@router.get("/teams/{team_id}/stats")
def team_stats(team_id: int) -> Dict[str, Any]:
    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        rows = list(session.exec(select(Power5Link).where(Power5Link.power_team_id == team_id)).all())
        by_status: Dict[str, int] = {}
        by_depth: Dict[int, int] = {}
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_depth[int(r.depth)] = by_depth.get(int(r.depth), 0) + 1

        return {
            "power_team_id": team_id,
            "leader_person_id": team.leader_person_id,
            "links_total": len(rows),
            "by_status": by_status,
            "by_depth": by_depth,
        }


@router.get("/teams/{team_id}/tree")
def team_tree(team_id: int) -> Dict[str, Any]:
    """
    Simple adjacency output for UI/Discord to render.
    """
    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        rows = list(session.exec(select(Power5Link).where(Power5Link.power_team_id == team_id)).all())
        children: Dict[int, list[dict]] = {}
        for r in rows:
            children.setdefault(r.parent_person_id, []).append(
                {"child_person_id": r.child_person_id, "status": r.status, "depth": r.depth}
            )

        return {
            "power_team_id": team_id,
            "leader_person_id": team.leader_person_id,
            "children": children,
        }


@router.post("/teams/{team_id}/invites")
def create_invite(
    team_id: int,
    invited_by_person_id: int,
    channel: str,
    destination: str,
    invitee_person_id: Optional[int] = None,
):
    raw_token = secrets.token_urlsafe(32)
    token_hash = _sha256(raw_token)

    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        inv = Power5Invite(
            power_team_id=team_id,
            invited_by_person_id=invited_by_person_id,
            invitee_person_id=invitee_person_id,
            channel=channel,
            destination=destination,
            token_hash=token_hash,
        )
        session.add(inv)
        session.commit()
        session.refresh(inv)

        # return token ONCE
        return {
            "invite_id": inv.id,
            "power_team_id": team_id,
            "expires_at": inv.expires_at.isoformat(),
            "token": raw_token,
        }


@router.post("/invites/consume")
def consume_invite(token: str):
    token_hash = _sha256(token)

    with get_session() as session:
        inv = session.exec(select(Power5Invite).where(Power5Invite.token_hash == token_hash)).first()
        if not inv:
            raise HTTPException(status_code=404, detail="Invite not found")
        if inv.consumed_at is not None:
            raise HTTPException(status_code=400, detail="Invite already consumed")
        if inv.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Invite expired")

        inv.consumed_at = datetime.utcnow()
        session.add(inv)
        session.commit()
        session.refresh(inv)

        return {
            "invite_id": inv.id,
            "power_team_id": inv.power_team_id,
            "invited_by_person_id": inv.invited_by_person_id,
            "invitee_person_id": inv.invitee_person_id,
            "channel": inv.channel,
            "destination": inv.destination,
            "consumed_at": inv.consumed_at.isoformat(),
        }
