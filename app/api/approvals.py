from __future__ import annotations

from typing import Optional, Any, Dict, Literal, List, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..database import get_session
from ..models.approval_request import ApprovalRequest, ApprovalStatus, ApprovalType
from ..models.person import Person, VolunteerStage, utcnow
from ..services.stage_engine import apply_stage_change

router = APIRouter(prefix="/approvals", tags=["approvals"])


# -------------------------
# Schemas
# -------------------------

class ApprovalRequestCreate(BaseModel):
    """
    Create an approval request.

    Backward-compatible request_type:
      - accepts: team | fundraising | leader
      - accepts: team_access | fundraising_access | leader_access
    """
    person_id: Optional[int] = None
    discord_user_id: Optional[str] = None

    # Optional profile hints (used only if creating a Person)
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    # NOTE: keep as str so we can accept both legacy + canonical strings
    request_type: str
    notes: Optional[str] = None

    # Optional metadata (safe to store; helps trace Discord request context)
    # NOTE: ApprovalRequest model may not yet have a meta/json column; we accept for forward-compat.
    meta: Optional[Dict[str, Any]] = None


class ApprovalReview(BaseModel):
    reviewer_person_id: Optional[int] = None
    reviewed_by_discord_user_id: Optional[str] = None
    reviewed_by_name: Optional[str] = None

    decision: Literal["approve", "deny"]

    # bot uses "reason" (older bot builds); newer uses "notes"
    reason: Optional[str] = None
    notes: Optional[str] = None


class ApprovalReviewResponse(BaseModel):
    approval: Dict[str, Any]
    stage_changed_to: Optional[str] = None


class ApprovalListResponse(BaseModel):
    items: List[Dict[str, Any]]


# -------------------------
# Helpers
# -------------------------

_APPROVAL_TYPE_ALIASES: Dict[str, ApprovalType] = {
    # legacy short forms
    "team": ApprovalType.TEAM,
    "fundraising": ApprovalType.FUNDRAISING,
    "leader": ApprovalType.LEADER,

    # canonical stored values
    "team_access": ApprovalType.TEAM,
    "fundraising_access": ApprovalType.FUNDRAISING,
    "leader_access": ApprovalType.LEADER,
}


def _parse_approval_type(raw: Optional[str]) -> Optional[ApprovalType]:
    """
    Accept both styles:
      - team / fundraising / leader
      - team_access / fundraising_access / leader_access

    Returns ApprovalType enum or None.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    return _APPROVAL_TYPE_ALIASES.get(s)


def _target_stage_for_request_type(rt: ApprovalType) -> VolunteerStage:
    if rt == ApprovalType.TEAM:
        return VolunteerStage.TEAM
    if rt == ApprovalType.FUNDRAISING:
        return VolunteerStage.FUNDRAISING
    if rt == ApprovalType.LEADER:
        return VolunteerStage.LEADER
    return VolunteerStage.TEAM


def _ensure_tracking_number(seed: str) -> str:
    suffix = (seed or "000000")[-6:]
    return f"TN-{utcnow().strftime('%Y%m%d')}-{suffix}"


def _find_person(
    session,
    *,
    person_id: Optional[int],
    discord_user_id: Optional[str],
) -> Optional[Person]:
    if person_id is not None:
        return session.get(Person, person_id)

    if discord_user_id:
        return session.exec(select(Person).where(Person.discord_user_id == discord_user_id)).first()

    return None


def _normalize_review_note(payload: ApprovalReview) -> Optional[str]:
    r = (payload.reason or "").strip()
    if r:
        return r
    n = (payload.notes or "").strip()
    return n or None


def _reviewer_person_from_payload(session, payload: ApprovalReview) -> Optional[Person]:
    if payload.reviewer_person_id is not None:
        return session.get(Person, payload.reviewer_person_id)

    if payload.reviewed_by_discord_user_id:
        p = session.exec(
            select(Person).where(Person.discord_user_id == payload.reviewed_by_discord_user_id)
        ).first()
        if p:
            return p

        p = Person(
            tracking_number=_ensure_tracking_number(payload.reviewed_by_discord_user_id),
            name=payload.reviewed_by_name or "Discord Admin",
            discord_user_id=payload.reviewed_by_discord_user_id,
            stage=VolunteerStage.OWNER,
            stage_locked=False,
            stage_last_changed_at=utcnow(),
            stage_changed_reason="auto:create_reviewer_from_discord",
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        return p

    return None


def _to_dict(req: ApprovalRequest) -> Dict[str, Any]:
    try:
        return req.model_dump()
    except Exception:
        return {
            "id": getattr(req, "id", None),
            "person_id": getattr(req, "person_id", None),
            "request_type": getattr(req, "request_type", None),
            "status": getattr(req, "status", None),
            "notes": getattr(req, "notes", None),
            "created_at": getattr(req, "created_at", None),
            "reviewed_at": getattr(req, "reviewed_at", None),
            "reviewed_by_person_id": getattr(req, "reviewed_by_person_id", None),
        }


# -------------------------
# Routes
# -------------------------

@router.post("/request", response_model=ApprovalRequest)
def create_approval_request(payload: ApprovalRequestCreate) -> ApprovalRequest:
    """
    Create a PENDING approval request.

    Backward compatible:
      request_type accepts:
        - team / fundraising / leader
        - team_access / fundraising_access / leader_access
    """
    rt = _parse_approval_type(payload.request_type)
    if not rt:
        raise HTTPException(
            status_code=400,
            detail="request_type must be team|fundraising|leader (or *_access variants).",
        )

    with get_session() as session:
        person = _find_person(
            session,
            person_id=payload.person_id,
            discord_user_id=payload.discord_user_id,
        )

        if not person:
            if not payload.discord_user_id:
                raise HTTPException(status_code=400, detail="Provide person_id or discord_user_id")

            person = Person(
                tracking_number=_ensure_tracking_number(payload.discord_user_id),
                name=payload.name or "Discord Volunteer",
                email=payload.email,
                phone=payload.phone,
                discord_user_id=payload.discord_user_id,
                stage=VolunteerStage.NEW,
                stage_locked=False,
                stage_last_changed_at=utcnow(),
                stage_changed_reason="auto:create_from_discord",
            )
            session.add(person)
            session.commit()
            session.refresh(person)

        existing = session.exec(
            select(ApprovalRequest).where(
                ApprovalRequest.person_id == person.id,
                ApprovalRequest.request_type == rt,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        ).first()
        if existing:
            return existing

        req = ApprovalRequest(
            person_id=person.id,
            request_type=rt,
            status=ApprovalStatus.PENDING,
            notes=payload.notes,
            created_at=utcnow(),
        )

        session.add(req)
        session.commit()
        session.refresh(req)
        return req


@router.get("/", response_model=list[ApprovalRequest])
def list_requests(
    status: Optional[ApprovalStatus] = None,
    request_type: Optional[str] = None,  # <-- accept both styles
    person_id: Optional[int] = None,
    limit: int = 200,
) -> list[ApprovalRequest]:
    """
    List approval requests.

    Backward compatible:
      /approvals/?status=pending&request_type=team&limit=20
      /approvals/?status=pending&request_type=team_access&limit=20
    """
    limit = max(1, min(int(limit or 200), 500))
    rt = _parse_approval_type(request_type)
    if request_type and not rt:
        raise HTTPException(
            status_code=400,
            detail="request_type must be team|fundraising|leader (or *_access variants).",
        )

    with get_session() as session:
        q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
        if status:
            q = q.where(ApprovalRequest.status == status)
        if rt:
            q = q.where(ApprovalRequest.request_type == rt)
        if person_id:
            q = q.where(ApprovalRequest.person_id == person_id)
        q = q.limit(limit)
        return list(session.exec(q).all())


@router.get("/pending", response_model=ApprovalListResponse)
def pending_requests(
    limit: int = 20,
    request_type: Optional[str] = None,  # <-- accept both styles
) -> ApprovalListResponse:
    """
    Bot-friendly pending list endpoint (legacy / convenience).

    Backward compatible:
      /approvals/pending?request_type=team
      /approvals/pending?request_type=team_access
    """
    limit = max(1, min(int(limit or 20), 50))
    rt = _parse_approval_type(request_type)
    if request_type and not rt:
        raise HTTPException(
            status_code=400,
            detail="request_type must be team|fundraising|leader (or *_access variants).",
        )

    with get_session() as session:
        q = select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)
        if rt:
            q = q.where(ApprovalRequest.request_type == rt)

        q = q.order_by(ApprovalRequest.created_at.desc()).limit(limit)
        rows = list(session.exec(q).all())

        items: List[Dict[str, Any]] = []
        for r in rows:
            p = session.get(Person, r.person_id)
            d = _to_dict(r)
            if p:
                d["name"] = p.name
                d["discord_user_id"] = p.discord_user_id
                d["email"] = p.email
                d["phone"] = p.phone
                d["stage"] = str(p.stage) if getattr(p, "stage", None) is not None else None
            items.append(d)

        return ApprovalListResponse(items=items)


@router.get("/{approval_id}", response_model=ApprovalRequest)
def get_request(approval_id: int) -> ApprovalRequest:
    with get_session() as session:
        req = session.get(ApprovalRequest, approval_id)
        if not req:
            raise HTTPException(status_code=404, detail="ApprovalRequest not found")
        return req


@router.post("/{approval_id}/review", response_model=ApprovalReviewResponse)
def review_request(approval_id: int, payload: ApprovalReview) -> ApprovalReviewResponse:
    with get_session() as session:
        req = session.get(ApprovalRequest, approval_id)
        if not req:
            raise HTTPException(status_code=404, detail="ApprovalRequest not found")

        if req.status != ApprovalStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"Request already {req.status}")

        person = session.get(Person, req.person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        reviewer = _reviewer_person_from_payload(session, payload)
        if not reviewer:
            raise HTTPException(
                status_code=400,
                detail="Provide reviewer_person_id or reviewed_by_discord_user_id",
            )

        decision = (payload.decision or "").strip().lower()
        if decision not in ("approve", "deny"):
            raise HTTPException(status_code=400, detail="decision must be approve|deny")

        review_note = _normalize_review_note(payload)

        if decision == "deny":
            req.status = ApprovalStatus.DENIED
            req.reviewed_at = utcnow()
            req.reviewed_by_person_id = reviewer.id
            req.notes = review_note or req.notes
            session.add(req)
            session.commit()
            session.refresh(req)
            return ApprovalReviewResponse(approval=_to_dict(req), stage_changed_to=None)

        req.status = ApprovalStatus.APPROVED
        req.reviewed_at = utcnow()
        req.reviewed_by_person_id = reviewer.id
        req.notes = review_note or req.notes
        session.add(req)
        session.commit()
        session.refresh(req)

        target_stage = _target_stage_for_request_type(cast(ApprovalType, req.request_type))

        apply_stage_change(
            session=session,
            person=person,
            new_stage=target_stage,
            reason=f"approved:{req.request_type}",
            lock_stage=True,
        )

        return ApprovalReviewResponse(approval=_to_dict(req), stage_changed_to=str(target_stage))
