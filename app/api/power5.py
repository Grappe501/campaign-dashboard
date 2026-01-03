from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlmodel import select

from ..database import get_session
from ..models.power_team import PowerTeam
from ..models.power5_link import Power5Link
from ..models.power5_invite import Power5Invite

router = APIRouter(prefix="/power5", tags=["power5"])


# -------------------------
# Schemas (bot/UI friendly)
# -------------------------

class Power5LinkUpsert(BaseModel):
    """
    Schema-based upsert to avoid clients passing DB-only fields.

    Note: power_team_id must match the path param (if provided).
    """
    power_team_id: Optional[int] = None
    parent_person_id: int = PydField(..., ge=1)
    child_person_id: int = PydField(..., ge=1)

    # Keep as Optional[str] to remain tolerant of enum/string changes.
    status: Optional[str] = None


class Power5InviteCreate(BaseModel):
    """
    Create a new invite. Returns the raw token ONCE.
    """
    invited_by_person_id: int = PydField(..., ge=1)
    channel: str = PydField(default="discord")
    destination: str = PydField(..., min_length=1)
    invitee_person_id: Optional[int] = None


class Power5InviteResponse(BaseModel):
    invite_id: int
    power_team_id: int
    expires_at: str
    token: str


class Power5ConsumeRequest(BaseModel):
    token: str = PydField(..., min_length=10)


class Power5ConsumeResponse(BaseModel):
    invite_id: int
    power_team_id: int
    invited_by_person_id: int
    invitee_person_id: Optional[int] = None
    channel: str
    destination: str
    consumed_at: str


# -------------------------
# Helpers
# -------------------------

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _utcnow_naive() -> datetime:
    """
    Keep naive UTC datetimes for DB compatibility if your models use naive UTC.
    """
    return datetime.utcnow().replace(tzinfo=None)


def _normalize_channel(channel: str) -> str:
    c = (channel or "").strip().lower()
    if c in ("email", "e-mail"):
        return "email"
    if c in ("sms", "text"):
        return "sms"
    if c in ("discord", "dm"):
        return "discord"
    return c or "discord"


def _is_invite_expired(inv: Power5Invite) -> bool:
    """
    Expiration check that tolerates:
      - missing expires_at
      - naive expires_at
      - tz-aware expires_at
    """
    exp = getattr(inv, "expires_at", None)
    if exp is None:
        return False
    try:
        if getattr(exp, "tzinfo", None) is not None:
            return exp < datetime.now(timezone.utc)
        return exp < _utcnow_naive()
    except Exception:
        return False


def _compute_depth(session, team_id: int, parent_person_id: int) -> int:
    team = session.get(PowerTeam, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    leader_id = getattr(team, "leader_person_id", None)
    if leader_id is None:
        raise HTTPException(status_code=500, detail="Team model missing leader_person_id")

    # If parent is the leader, child is depth 1
    if parent_person_id == int(leader_id):
        return 1

    # Otherwise, depth = parent's depth + 1 (must exist)
    q = select(Power5Link).where(
        Power5Link.power_team_id == team_id,
        Power5Link.child_person_id == parent_person_id,
    )
    parent_link = session.exec(q).first()
    if not parent_link:
        # strict: avoids ambiguous trees
        raise HTTPException(status_code=400, detail="Parent is not in the Power5 tree for this team")

    return int(getattr(parent_link, "depth", 0) or 0) + 1


def _set_if_present(obj: Any, field: str, value: Any) -> None:
    """
    Set obj.field=value only if the attribute exists on the object.
    Defensive against schema drift across milestones.
    """
    try:
        if hasattr(obj, field):
            setattr(obj, field, value)
    except Exception:
        return


def _safe_iso(dt: Any) -> str:
    try:
        return dt.isoformat()
    except Exception:
        return ""


# -------------------------
# Routes
# -------------------------

@router.post("/teams/{team_id}/links", response_model=Power5Link)
def upsert_link(team_id: int, payload: Power5LinkUpsert) -> Power5Link:
    """
    Upsert a tree link for a team.

    Rules:
    - Enforces one child per team (uniqueness).
    - Computes depth deterministically from parent chain.
    - Rejects child == parent.
    """
    if payload.power_team_id is not None and payload.power_team_id != team_id:
        raise HTTPException(status_code=400, detail="power_team_id mismatch")

    if payload.child_person_id == payload.parent_person_id:
        raise HTTPException(status_code=400, detail="child_person_id cannot equal parent_person_id")

    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        existing = session.exec(
            select(Power5Link).where(
                Power5Link.power_team_id == team_id,
                Power5Link.child_person_id == payload.child_person_id,
            )
        ).first()

        depth = _compute_depth(session, team_id, payload.parent_person_id)

        if existing:
            existing.parent_person_id = payload.parent_person_id
            _set_if_present(existing, "depth", depth)
            if payload.status is not None:
                _set_if_present(existing, "status", payload.status)

            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        link = Power5Link(
            power_team_id=team_id,
            parent_person_id=payload.parent_person_id,
            child_person_id=payload.child_person_id,
        )
        _set_if_present(link, "depth", depth)
        if payload.status is not None:
            _set_if_present(link, "status", payload.status)

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
            st = str(getattr(r, "status", "") or "")
            by_status[st] = by_status.get(st, 0) + 1

            d = int(getattr(r, "depth", 0) or 0)
            by_depth[d] = by_depth.get(d, 0) + 1

        return {
            "power_team_id": team_id,
            "leader_person_id": getattr(team, "leader_person_id", None),
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
        children: Dict[int, List[Dict[str, Any]]] = {}

        for r in rows:
            parent_id = int(getattr(r, "parent_person_id", 0) or 0)
            children.setdefault(parent_id, []).append(
                {
                    "child_person_id": getattr(r, "child_person_id", None),
                    "status": getattr(r, "status", None),
                    "depth": getattr(r, "depth", None),
                }
            )

        return {
            "power_team_id": team_id,
            "leader_person_id": getattr(team, "leader_person_id", None),
            "children": children,
        }


@router.post("/teams/{team_id}/invites", response_model=Power5InviteResponse)
def create_invite(team_id: int, payload: Power5InviteCreate) -> Power5InviteResponse:
    raw_token = secrets.token_urlsafe(32)
    token_hash = _sha256(raw_token)
    ch = _normalize_channel(payload.channel)

    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        inv = Power5Invite(
            power_team_id=team_id,
            invited_by_person_id=payload.invited_by_person_id,
        )

        # Forward-compatible optional fields
        _set_if_present(inv, "invitee_person_id", payload.invitee_person_id)
        _set_if_present(inv, "channel", ch)
        _set_if_present(inv, "destination", payload.destination)
        _set_if_present(inv, "token_hash", token_hash)

        session.add(inv)
        session.commit()
        session.refresh(inv)

        expires_at = _safe_iso(getattr(inv, "expires_at", None)) if getattr(inv, "expires_at", None) else ""

        return Power5InviteResponse(
            invite_id=int(getattr(inv, "id", 0) or 0),
            power_team_id=team_id,
            expires_at=expires_at,
            token=raw_token,
        )


@router.post("/invites/consume", response_model=Power5ConsumeResponse)
def consume_invite(payload: Power5ConsumeRequest) -> Power5ConsumeResponse:
    """
    Consume an invite token (one-time).
    """
    token_hash = _sha256(payload.token)

    with get_session() as session:
        # If token_hash doesn't exist in your model yet, this will throw at query-build time.
        # That is OK: it means your Power5Invite model is older and needs the token_hash field.
        inv = session.exec(select(Power5Invite).where(Power5Invite.token_hash == token_hash)).first()  # type: ignore[attr-defined]
        if not inv:
            raise HTTPException(status_code=404, detail="Invite not found")

        if getattr(inv, "consumed_at", None) is not None:
            raise HTTPException(status_code=400, detail="Invite already consumed")

        if _is_invite_expired(inv):
            raise HTTPException(status_code=400, detail="Invite expired")

        now = _utcnow_naive()
        _set_if_present(inv, "consumed_at", now)

        session.add(inv)
        session.commit()
        session.refresh(inv)

        consumed_at = _safe_iso(getattr(inv, "consumed_at", None)) or now.isoformat()

        return Power5ConsumeResponse(
            invite_id=int(getattr(inv, "id", 0) or 0),
            power_team_id=int(getattr(inv, "power_team_id", 0) or 0),
            invited_by_person_id=int(getattr(inv, "invited_by_person_id", 0) or 0),
            invitee_person_id=getattr(inv, "invitee_person_id", None),
            channel=str(getattr(inv, "channel", "") or ""),
            destination=str(getattr(inv, "destination", "") or ""),
            consumed_at=consumed_at,
        )
