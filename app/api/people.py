from __future__ import annotations

from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field as PydField
from sqlmodel import select

from ..database import get_session
from ..models.person import Person, VolunteerStage, utcnow
from ..services.impact_engine import compute_impact
from ..services.stage_engine import apply_stage_change

router = APIRouter(prefix="/people", tags=["people"])


# -----------------------------
# Schemas (do NOT use DB model as input)
# -----------------------------

class PersonCreate(BaseModel):
    tracking_number: str = PydField(..., min_length=3)
    name: str = PydField(..., min_length=1)

    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    discord_user_id: Optional[str] = None

    # lifecycle (safe only)
    stage: Optional[VolunteerStage] = VolunteerStage.OBSERVER

    # geography
    region: Optional[str] = None
    county: Optional[str] = None
    city: Optional[str] = None
    precinct: Optional[str] = None

    recruited_by_person_id: Optional[int] = None

    # consent flags
    allow_tracking: bool = True
    allow_discord_comms: bool = True
    allow_leaderboard: bool = True


class PersonPatch(BaseModel):
    """
    Partial update. Any field omitted is left unchanged.
    """
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    discord_user_id: Optional[str] = None

    # safe stage changes only; gated stages rejected
    stage: Optional[VolunteerStage] = None

    # geography
    region: Optional[str] = None
    county: Optional[str] = None
    city: Optional[str] = None
    precinct: Optional[str] = None

    recruited_by_person_id: Optional[int] = None

    # consent flags
    allow_tracking: Optional[bool] = None
    allow_discord_comms: Optional[bool] = None
    allow_leaderboard: Optional[bool] = None


class PersonReplace(BaseModel):
    """
    Full replace payload (legacy PUT semantics), but schema-based so clients
    cannot pass DB-only fields like stage_locked or audit fields.

    tracking_number is included but must match the existing record (immutable).
    """
    tracking_number: str = PydField(..., min_length=3)
    name: str = PydField(..., min_length=1)

    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    discord_user_id: Optional[str] = None

    # lifecycle (safe only)
    stage: VolunteerStage = VolunteerStage.OBSERVER

    # geography
    region: Optional[str] = None
    county: Optional[str] = None
    city: Optional[str] = None
    precinct: Optional[str] = None

    recruited_by_person_id: Optional[int] = None

    # consent flags (required on PUT to keep "replace" meaning)
    allow_tracking: bool = True
    allow_discord_comms: bool = True
    allow_leaderboard: bool = True


# -----------------------------
# Guardrails / normalization
# -----------------------------

_GATED_STAGES = {
    VolunteerStage.TEAM,
    VolunteerStage.FUNDRAISING,
    VolunteerStage.LEADER,
    VolunteerStage.ADMIN,
}


def _ensure_not_gated(stage: VolunteerStage) -> None:
    if stage in _GATED_STAGES:
        raise HTTPException(
            status_code=403,
            detail="Stage change to gated levels must go through approvals.",
        )


def _ensure_stage_unlocked_for_manual_change(p: Person) -> None:
    """
    If approvals has locked the stage, normal /people updates must not change it.
    (Approvals workflows own locking/unlocking.)
    """
    if bool(getattr(p, "stage_locked", False)):
        raise HTTPException(
            status_code=403,
            detail="Stage is locked (managed by approvals).",
        )


def _normalize_stage(stage: Optional[VolunteerStage]) -> VolunteerStage:
    """
    Defensive helper so we always work with a real enum member.
    """
    return stage or VolunteerStage.OBSERVER


# -----------------------------
# Routes
# -----------------------------

@router.post("/", response_model=Person)
def create_person(payload: PersonCreate) -> Person:
    """
    Create a person.

    Guardrails:
    - Prevent creating directly into gated stages (TEAM/FUNDRAISING/LEADER/ADMIN).
    - Initialize stage audit fields.
    """
    stage = _normalize_stage(payload.stage)
    _ensure_not_gated(stage)

    person = Person(
        tracking_number=payload.tracking_number,
        name=payload.name,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        discord_user_id=payload.discord_user_id,
        stage=stage,
        # approvals-owned fields (safe defaults)
        stage_locked=False,
        stage_last_changed_at=utcnow(),
        stage_changed_reason="manual:create",
        region=payload.region,
        county=payload.county,
        city=payload.city,
        precinct=payload.precinct,
        recruited_by_person_id=payload.recruited_by_person_id,
        allow_tracking=payload.allow_tracking,
        allow_discord_comms=payload.allow_discord_comms,
        allow_leaderboard=payload.allow_leaderboard,
    )

    with get_session() as session:
        existing = session.exec(
            select(Person).where(Person.tracking_number == person.tracking_number)
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="tracking_number already exists")

        session.add(person)
        session.commit()
        session.refresh(person)
        return person


@router.get("/", response_model=List[Person])
def list_people(
    limit: int = 200,
    offset: int = 0,
    stage: Optional[VolunteerStage] = None,
    county: Optional[str] = None,
    discord_user_id: Optional[str] = None,
) -> List[Person]:
    """
    List people with basic filtering + pagination.

    IMPORTANT:
    - We sort by id desc (NOT created_at) to avoid schema-mismatch 500s if your
      SQLite people table was created before created_at existed.
    """
    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000
    if offset < 0:
        offset = 0

    with get_session() as session:
        q = select(Person)

        if stage is not None:
            q = q.where(Person.stage == stage)
        if county:
            q = q.where(Person.county == county)
        if discord_user_id:
            q = q.where(Person.discord_user_id == discord_user_id)

        q = q.order_by(Person.id.desc()).offset(offset).limit(limit)
        return list(session.exec(q).all())


@router.get("/{person_id}", response_model=Person)
def get_person(person_id: int) -> Person:
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        return p


@router.get("/by_tracking/{tracking_number}", response_model=Person)
def get_person_by_tracking(tracking_number: str) -> Person:
    """
    Convenience endpoint (future-proofing): fetch by tracking_number.
    """
    with get_session() as session:
        p = session.exec(select(Person).where(Person.tracking_number == tracking_number)).first()
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        return p


@router.patch("/{person_id}", response_model=Person)
def patch_person(person_id: int, payload: PersonPatch) -> Person:
    """
    Partial update.

    Guardrails:
    - Cannot set gated stages via this endpoint.
    - Cannot change stage if stage_locked=True.
    - Stage transitions (safe) go through apply_stage_change for audit consistency.
    """
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")

        # Stage change (safe only)
        if payload.stage is not None and payload.stage != p.stage:
            _ensure_stage_unlocked_for_manual_change(p)
            _ensure_not_gated(payload.stage)
            apply_stage_change(
                session=session,
                person=p,
                new_stage=payload.stage,
                reason="manual:patch",
                lock_stage=False,
            )

        # Scalar updates (only if provided)
        if payload.name is not None:
            p.name = payload.name
        if payload.email is not None:
            p.email = str(payload.email)
        if payload.phone is not None:
            p.phone = payload.phone
        if payload.discord_user_id is not None:
            p.discord_user_id = payload.discord_user_id

        if payload.region is not None:
            p.region = payload.region
        if payload.county is not None:
            p.county = payload.county
        if payload.city is not None:
            p.city = payload.city
        if payload.precinct is not None:
            p.precinct = payload.precinct

        if payload.recruited_by_person_id is not None:
            p.recruited_by_person_id = payload.recruited_by_person_id

        if payload.allow_tracking is not None:
            p.allow_tracking = payload.allow_tracking
        if payload.allow_discord_comms is not None:
            p.allow_discord_comms = payload.allow_discord_comms
        if payload.allow_leaderboard is not None:
            p.allow_leaderboard = payload.allow_leaderboard

        session.add(p)
        session.commit()
        session.refresh(p)
        return p


@router.put("/{person_id}", response_model=Person)
def replace_person(person_id: int, payload: PersonReplace) -> Person:
    """
    Full replace (legacy compatibility), but schema-based to prevent bypass.

    Rules:
    - tracking_number is immutable (must match existing).
    - stage cannot be set to gated stages here.
    - cannot change stage if stage_locked=True.
    - stage transitions (safe) go through apply_stage_change for audit consistency.
    """
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")

        if payload.tracking_number != p.tracking_number:
            raise HTTPException(status_code=400, detail="tracking_number is immutable.")

        if payload.stage != p.stage:
            _ensure_stage_unlocked_for_manual_change(p)
            _ensure_not_gated(payload.stage)
            apply_stage_change(
                session=session,
                person=p,
                new_stage=payload.stage,
                reason="manual:put",
                lock_stage=False,
            )

        # Overwrite allowed fields (PUT semantics)
        p.name = payload.name
        p.email = str(payload.email) if payload.email else None
        p.phone = payload.phone
        p.discord_user_id = payload.discord_user_id

        p.region = payload.region
        p.county = payload.county
        p.city = payload.city
        p.precinct = payload.precinct
        p.recruited_by_person_id = payload.recruited_by_person_id

        p.allow_tracking = payload.allow_tracking
        p.allow_discord_comms = payload.allow_discord_comms
        p.allow_leaderboard = payload.allow_leaderboard

        session.add(p)
        session.commit()
        session.refresh(p)
        return p


@router.get("/{person_id}/impact")
def get_person_impact(person_id: int) -> Dict[str, Any]:
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        summary = compute_impact(session, person_id)
        return summary.__dict__
