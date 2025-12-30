from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any, Dict, Literal

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

    Discord-first:
    - If person_id is missing but discord_user_id is provided, the API will create a Person row if needed.
    - The bot should usually use discord_user_id (and a display name).
    """
    person_id: Optional[int] = None
    discord_user_id: Optional[str] = None

    # Optional profile hints (used only if creating a Person)
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    request_type: ApprovalType
    notes: Optional[str] = None


class ApprovalReview(BaseModel):
    """
    Review an approval request.
    reviewer_person_id must be a Person (people.id) in the database.
    """
    reviewer_person_id: int
    decision: Literal["approve", "deny"]
    notes: Optional[str] = None


class ApprovalReviewResponse(BaseModel):
    approval: Dict[str, Any]
    stage_changed_to: Optional[str] = None


# -------------------------
# Helpers
# -------------------------

def _target_stage_for_request_type(rt: ApprovalType) -> VolunteerStage:
    if rt == ApprovalType.TEAM:
        return VolunteerStage.TEAM
    if rt == ApprovalType.FUNDRAISING:
        return VolunteerStage.FUNDRAISING
    if rt == ApprovalType.LEADER:
        return VolunteerStage.LEADER
    return VolunteerStage.TEAM


def _ensure_tracking_number(seed: str) -> str:
    """
    Lightweight TN for discord-first creation. Replace later if you adopt a canonical format.
    Example: TN-20251229-123456
    """
    suffix = (seed or "000000")[-6:]
    return f"TN-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{suffix}"


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


# -------------------------
# Routes
# -------------------------

@router.post("/request", response_model=ApprovalRequest)
def create_approval_request(payload: ApprovalRequestCreate) -> ApprovalRequest:
    """
    Create a PENDING approval request (TEAM / FUNDRAISING / LEADER).

    Dedupe rule:
      - If an existing PENDING request exists for (person_id, request_type), return it.

    Discord-first:
      - If person doesn't exist and discord_user_id is provided, create Person(stage=NEW).
    """
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
                ApprovalRequest.request_type == payload.request_type,
                ApprovalRequest.status == ApprovalStatus.PENDING,
            )
        ).first()
        if existing:
            return existing

        req = ApprovalRequest(
            person_id=person.id,
            request_type=payload.request_type,
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
    request_type: Optional[ApprovalType] = None,
    person_id: Optional[int] = None,
    limit: int = 200,
) -> list[ApprovalRequest]:
    """
    List approval requests.
    """
    with get_session() as session:
        q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
        if status:
            q = q.where(ApprovalRequest.status == status)
        if request_type:
            q = q.where(ApprovalRequest.request_type == request_type)
        if person_id:
            q = q.where(ApprovalRequest.person_id == person_id)
        q = q.limit(limit)
        return list(session.exec(q).all())


@router.get("/{approval_id}", response_model=ApprovalRequest)
def get_request(approval_id: int) -> ApprovalRequest:
    with get_session() as session:
        req = session.get(ApprovalRequest, approval_id)
        if not req:
            raise HTTPException(status_code=404, detail="ApprovalRequest not found")
        return req


@router.post("/{approval_id}/review", response_model=ApprovalReviewResponse)
def review_request(approval_id: int, payload: ApprovalReview) -> ApprovalReviewResponse:
    """
    Approve/deny a request.

    If approved:
      - Person.stage is set to TEAM/FUNDRAISING/LEADER
      - stage is locked (stage_locked=True) to prevent auto-promotion skipping gates
    """
    with get_session() as session:
        req = session.get(ApprovalRequest, approval_id)
        if not req:
            raise HTTPException(status_code=404, detail="ApprovalRequest not found")

        if req.status != ApprovalStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"Request already {req.status}")

        person = session.get(Person, req.person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        reviewer = session.get(Person, payload.reviewer_person_id)
        if not reviewer:
            raise HTTPException(status_code=404, detail="Reviewer person not found")

        decision = payload.decision.strip().lower()
        if decision not in ("approve", "deny"):
            raise HTTPException(status_code=400, detail="decision must be approve|deny")

        if decision == "deny":
            req.status = ApprovalStatus.DENIED
            req.reviewed_at = utcnow()
            req.reviewed_by_person_id = payload.reviewer_person_id
            req.notes = payload.notes or req.notes
            session.add(req)
            session.commit()
            session.refresh(req)
            return ApprovalReviewResponse(approval=req.model_dump(), stage_changed_to=None)

        # approve
        req.status = ApprovalStatus.APPROVED
        req.reviewed_at = utcnow()
        req.reviewed_by_person_id = payload.reviewer_person_id
        req.notes = payload.notes or req.notes
        session.add(req)
        session.commit()
        session.refresh(req)

        target_stage = _target_stage_for_request_type(req.request_type)

        apply_stage_change(
            session=session,
            person=person,
            new_stage=target_stage,
            reason=f"approved:{req.request_type}",
            lock_stage=True,
        )

        return ApprovalReviewResponse(approval=req.model_dump(), stage_changed_to=str(target_stage))
