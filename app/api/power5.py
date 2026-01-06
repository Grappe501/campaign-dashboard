from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.power5_invite import Power5Invite
from ..models.power5_link import POWER5_STATUSES, normalize_status
from ..models.power5_link import Power5Link
from ..models.power_team import PowerTeam

logger = logging.getLogger(__name__)

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
# Leader-friendly endpoints (no team_id required)
# -------------------------


class Power5LeaderInviteCreate(BaseModel):
    """
    Create an invite for the leader’s own PowerTeam without needing team_id.
    Leader only needs their person_id + destination.
    """

    leader_person_id: int = PydField(..., ge=1)
    channel: str = PydField(default="discord")
    destination: str = PydField(..., min_length=1)
    invitee_person_id: Optional[int] = None


class Power5InviteClaim(BaseModel):
    """
    Claim an invite token and (optionally) link the invitee into the team tree.
    """

    token: str = PydField(..., min_length=10)
    invitee_person_id: int = PydField(..., ge=1)
    status: Optional[str] = PydField(default="onboarded")


class Power5InviteClaimResponse(BaseModel):
    invite: Power5ConsumeResponse
    link: Optional[Power5Link] = None


# -------------------------
# Helpers
# -------------------------


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _utcnow_naive() -> datetime:
    # Keep naive UTC datetimes for DB compatibility (SQLite + existing models).
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


def _get_or_create_team_for_leader(session, leader_person_id: int) -> PowerTeam:
    """
    Resolve the PowerTeam for a leader (person_id). If none exists, create one.

    Handles uniqueness/race safely (expects leader_person_id uniqueness at DB level).
    """
    q = select(PowerTeam).where(PowerTeam.leader_person_id == leader_person_id)  # type: ignore[attr-defined]
    team = session.exec(q).first()
    if team:
        return team

    team = PowerTeam(leader_person_id=leader_person_id)
    _set_if_present(team, "name", f"Power of 5 — Leader {leader_person_id}")
    _set_if_present(team, "created_at", _utcnow_naive())
    _set_if_present(team, "updated_at", _utcnow_naive())

    session.add(team)
    try:
        session.commit()
    except IntegrityError:
        # Another request likely created it first; re-fetch deterministically.
        session.rollback()
        team2 = session.exec(q).first()
        if team2:
            return team2
        raise HTTPException(status_code=500, detail="Failed to create/find team for leader") from None

    session.refresh(team)
    return team


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

    # Otherwise, depth = parent's depth + 1 (parent must already exist)
    q = select(Power5Link).where(
        Power5Link.power_team_id == team_id,
        Power5Link.child_person_id == parent_person_id,
    )
    parent_link = session.exec(q).first()
    if not parent_link:
        raise HTTPException(status_code=400, detail="Parent is not in the Power5 tree for this team")

    return int(getattr(parent_link, "depth", 0) or 0) + 1


def _create_invite_row(
    session,
    *,
    team_id: int,
    invited_by_person_id: int,
    channel: str,
    destination: str,
    invitee_person_id: Optional[int],
) -> Power5InviteResponse:
    """
    Create a Power5Invite row and return raw token ONCE.

    Retries on rare token_hash collisions (unique index).
    """
    ch = _normalize_channel(channel)
    dest = (destination or "").strip()
    if not dest:
        raise HTTPException(status_code=400, detail="destination is required")

    last_err: Optional[Exception] = None

    for _ in range(3):
        raw_token = secrets.token_urlsafe(32)
        token_hash = _sha256(raw_token)

        inv = Power5Invite(
            power_team_id=team_id,
            invited_by_person_id=invited_by_person_id,
        )

        _set_if_present(inv, "invitee_person_id", invitee_person_id)
        _set_if_present(inv, "channel", ch)
        _set_if_present(inv, "destination", dest)
        _set_if_present(inv, "token_hash", token_hash)

        session.add(inv)
        try:
            session.commit()
        except IntegrityError as e:
            # Either a token_hash collision or FK/unique constraint issue.
            session.rollback()
            last_err = e
            continue

        session.refresh(inv)
        expires_at = _safe_iso(getattr(inv, "expires_at", None)) if getattr(inv, "expires_at", None) else ""

        return Power5InviteResponse(
            invite_id=int(getattr(inv, "id", 0) or 0),
            power_team_id=team_id,
            expires_at=expires_at,
            token=raw_token,
        )

    logger.exception("Power5 invite create failed after retries", exc_info=last_err)
    raise HTTPException(status_code=409, detail="Invite could not be created (try again)") from None


def _consume_invite_row(session, token: str) -> Power5Invite:
    token_hash = _sha256(token)

    inv = session.exec(
        select(Power5Invite).where(Power5Invite.token_hash == token_hash)  # type: ignore[attr-defined]
    ).first()
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

    return inv


def _to_consume_response(inv: Power5Invite) -> Power5ConsumeResponse:
    now = _utcnow_naive()
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


# -------------------------
# Routes
# -------------------------


@router.post("/teams/{team_id}/links", response_model=Power5Link)
def upsert_link(team_id: int, payload: Power5LinkUpsert) -> Power5Link:
    if payload.power_team_id is not None and payload.power_team_id != team_id:
        raise HTTPException(status_code=400, detail="power_team_id mismatch")

    if payload.child_person_id == payload.parent_person_id:
        raise HTTPException(status_code=400, detail="child_person_id cannot equal parent_person_id")

    # Normalize status if provided (fail closed on invalid)
    st: Optional[str] = None
    if payload.status is not None:
        st = normalize_status(payload.status)

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
            if st is not None:
                _set_if_present(existing, "status", st)

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
        if st is not None:
            _set_if_present(link, "status", st)

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

        # Ensure all known statuses appear (nice for dashboards)
        for s in POWER5_STATUSES:
            by_status.setdefault(s, 0)

        return {
            "power_team_id": team_id,
            "leader_person_id": getattr(team, "leader_person_id", None),
            "links_total": len(rows),
            "by_status": by_status,
            "by_depth": by_depth,
        }


@router.get("/teams/{team_id}/tree")
def team_tree(team_id: int) -> Dict[str, Any]:
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
    with get_session() as session:
        team = session.get(PowerTeam, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        return _create_invite_row(
            session,
            team_id=team_id,
            invited_by_person_id=payload.invited_by_person_id,
            channel=payload.channel,
            destination=payload.destination,
            invitee_person_id=payload.invitee_person_id,
        )


@router.post("/invites/consume", response_model=Power5ConsumeResponse)
def consume_invite(payload: Power5ConsumeRequest) -> Power5ConsumeResponse:
    """
    Consume an invite token (one-time). Does not create links.
    """
    with get_session() as session:
        inv = _consume_invite_row(session, payload.token)
        return _to_consume_response(inv)


# -------------------------
# Leader-friendly routes
# -------------------------


@router.post("/invites/create", response_model=Power5InviteResponse)
def create_invite_for_leader(payload: Power5LeaderInviteCreate) -> Power5InviteResponse:
    """
    Create a Power of 5 invite without requiring team_id.
    """
    ch = _normalize_channel(payload.channel)
    dest = (payload.destination or "").strip()
    if not dest:
        raise HTTPException(status_code=400, detail="destination is required")

    with get_session() as session:
        team = _get_or_create_team_for_leader(session, int(payload.leader_person_id))
        tid = int(getattr(team, "id", 0) or 0)
        if tid < 1:
            raise HTTPException(status_code=500, detail="Failed to create/find team")

        return _create_invite_row(
            session,
            team_id=tid,
            invited_by_person_id=int(payload.leader_person_id),
            channel=ch,
            destination=dest,
            invitee_person_id=payload.invitee_person_id,
        )


@router.post("/invites/claim", response_model=Power5InviteClaimResponse)
def claim_invite(payload: Power5InviteClaim) -> Power5InviteClaimResponse:
    """
    Claim a token AND (best-effort) create/update the team link inviter -> invitee.

    Behavior:
    - Always consumes the invite if valid.
    - Attempts to create/update the Power5Link edge.
    - If linking fails, claim still succeeds (best-effort).
    """
    # Normalize/validate status; default to onboarded
    try:
        st = normalize_status(payload.status or "onboarded")
    except Exception:
        st = "onboarded"

    with get_session() as session:
        inv = _consume_invite_row(session, payload.token)

        # If invitee_person_id field exists on invite model and is empty, set it.
        if getattr(inv, "invitee_person_id", None) in (None, 0):
            _set_if_present(inv, "invitee_person_id", int(payload.invitee_person_id))
            session.add(inv)
            session.commit()
            session.refresh(inv)

        link_obj: Optional[Power5Link] = None

        # Best-effort linking — claiming should succeed even if linking fails.
        try:
            team_id = int(getattr(inv, "power_team_id", 0) or 0)
            inviter_id = int(getattr(inv, "invited_by_person_id", 0) or 0)
            invitee_id = int(payload.invitee_person_id)

            if team_id >= 1 and inviter_id >= 1 and invitee_id >= 1 and inviter_id != invitee_id:
                existing = session.exec(
                    select(Power5Link).where(
                        Power5Link.power_team_id == team_id,
                        Power5Link.child_person_id == invitee_id,
                    )
                ).first()

                depth = _compute_depth(session, team_id, inviter_id)

                if existing:
                    existing.parent_person_id = inviter_id
                    _set_if_present(existing, "depth", depth)
                    _set_if_present(existing, "status", st)
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                    link_obj = existing
                else:
                    link = Power5Link(
                        power_team_id=team_id,
                        parent_person_id=inviter_id,
                        child_person_id=invitee_id,
                    )
                    _set_if_present(link, "depth", depth)
                    _set_if_present(link, "status", st)
                    session.add(link)
                    session.commit()
                    session.refresh(link)
                    link_obj = link
        except Exception:
            logger.exception("Power5 invite claim: link creation failed (ignored)")
            link_obj = None

        return Power5InviteClaimResponse(
            invite=_to_consume_response(inv),
            link=link_obj,
        )
