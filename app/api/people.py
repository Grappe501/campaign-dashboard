from __future__ import annotations

from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field as PydField
from sqlmodel import select

from ..database import get_session
from ..models.person import Person, VolunteerStage, utcnow
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

    # Canonical access setters (optional; admin paths only).
    team_access: Optional[bool] = None
    fundraising_access: Optional[bool] = None
    leader_access: Optional[bool] = None

    # Legacy aliases (accepted for compatibility; normalized into canonical booleans)
    team: Optional[bool] = None
    fundraising: Optional[bool] = None
    leader: Optional[bool] = None


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

    # Canonical access flags (optional on PUT; if omitted, leave unchanged)
    team_access: Optional[bool] = None
    fundraising_access: Optional[bool] = None
    leader_access: Optional[bool] = None

    # Legacy aliases (optional; normalized)
    team: Optional[bool] = None
    fundraising: Optional[bool] = None
    leader: Optional[bool] = None


class DiscordUpsert(BaseModel):
    """
    Discord-first identity upsert. This is the backbone of onboarding.
    """
    discord_user_id: str = PydField(..., min_length=3)
    name: str = PydField(..., min_length=1)

    # Optional enrichment
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

    # Optional: link recruit source
    recruited_by_person_id: Optional[int] = None

    # Optional geo
    region: Optional[str] = None
    county: Optional[str] = None
    city: Optional[str] = None
    precinct: Optional[str] = None

    # Optional Discord context (for sync hardening / audit)
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    username: Optional[str] = None


class OnboardRequest(BaseModel):
    """
    Mark a person as onboarded + return next-step suggestions.
    """
    person_id: Optional[int] = None
    discord_user_id: Optional[str] = None

    # Optional Discord context (for sync hardening / audit)
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    username: Optional[str] = None

    # Optional enrichment
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    county: Optional[str] = None
    city: Optional[str] = None

    # Consent flags (optional)
    allow_discord_comms: Optional[bool] = None
    allow_tracking: Optional[bool] = None


class OnboardResponse(BaseModel):
    person: Dict[str, Any]
    next_steps: List[str]


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
    if bool(getattr(p, "stage_locked", False)):
        raise HTTPException(
            status_code=403,
            detail="Stage is locked (managed by approvals).",
        )


def _normalize_stage(stage: Optional[VolunteerStage]) -> VolunteerStage:
    return stage or VolunteerStage.OBSERVER


def _ensure_tracking_number(seed: str) -> str:
    suffix = (seed or "000000")[-6:]
    return f"TN-{utcnow().strftime('%Y%m%d')}-{suffix}"


def _person_to_dict(p: Person) -> Dict[str, Any]:
    try:
        d: Dict[str, Any] = p.model_dump()
    except Exception:
        d = {
            "id": p.id,
            "tracking_number": p.tracking_number,
            "name": p.name,
            "email": p.email,
            "phone": p.phone,
            "discord_user_id": p.discord_user_id,
            "stage": str(p.stage) if getattr(p, "stage", None) is not None else None,
            "stage_locked": bool(getattr(p, "stage_locked", False)),
            "onboarded_at": getattr(p, "onboarded_at", None),
            "county": getattr(p, "county", None),
            "city": getattr(p, "city", None),
        }

    d["team_access"] = bool(getattr(p, "team_access", False))
    d["fundraising_access"] = bool(getattr(p, "fundraising_access", False))
    d["leader_access"] = bool(getattr(p, "leader_access", False))
    d["is_admin"] = bool(getattr(p, "is_admin", False))

    # Legacy mirrors
    d["team"] = d["team_access"]
    d["fundraising"] = d["fundraising_access"]
    d["leader"] = d["leader_access"]

    return d


def _next_steps_for_person(p: Person) -> List[str]:
    steps: List[str] = []

    if p.stage in (VolunteerStage.OBSERVER, VolunteerStage.NEW):
        steps.append("Do one small action today (call/text/door/share) and log it as a win.")
        steps.append("Post an intro: your county + 1 way you can help this week.")
        steps.append("Ask your recruiter for a Power of 5 invite (or request one from a lead).")
        return steps

    if p.stage == VolunteerStage.ACTIVE:
        steps.append("Log one more action today and invite 1 friend to take an action with you.")
    if p.stage == VolunteerStage.OWNER:
        steps.append("Pick a lane for this week and bring 1 new person into the hub.")

    if getattr(p, "team_access", False) and p.stage in (
        VolunteerStage.TEAM,
        VolunteerStage.LEADER,
        VolunteerStage.ADMIN,
        VolunteerStage.FUNDRAISING,
    ):
        steps.append("Coordinate in your team lane and post daily wins.")
    else:
        steps.append("If you need deeper access, request TEAM access (human-approved).")

    if getattr(p, "fundraising_access", False):
        steps.append("Follow your fundraising lane plan and log each touch as an action.")
    else:
        steps.append("If you will help with fundraising, request FUNDRAISING access (human-approved).")

    if getattr(p, "leader_access", False):
        steps.append("If you own a lane, post today’s priorities and assign 1 task.")
    else:
        steps.append("If you’re leading a lane, request LEADER access (human-approved).")

    return steps


def _apply_access_updates(
    p: Person,
    *,
    team_access: Optional[bool] = None,
    fundraising_access: Optional[bool] = None,
    leader_access: Optional[bool] = None,
) -> bool:
    changed = False

    if team_access is not None and hasattr(p, "team_access"):
        nv = bool(team_access)
        if bool(getattr(p, "team_access", False)) != nv:
            p.team_access = nv
            changed = True

    if fundraising_access is not None and hasattr(p, "fundraising_access"):
        nv = bool(fundraising_access)
        if bool(getattr(p, "fundraising_access", False)) != nv:
            p.fundraising_access = nv
            changed = True

    if leader_access is not None and hasattr(p, "leader_access"):
        nv = bool(leader_access)
        if bool(getattr(p, "leader_access", False)) != nv:
            p.leader_access = nv
            changed = True

    return changed


# -----------------------------
# Routes
# -----------------------------

@router.post("/", response_model=Person)
def create_person(payload: PersonCreate) -> Person:
    stage = _normalize_stage(payload.stage)
    _ensure_not_gated(stage)

    person = Person(
        tracking_number=payload.tracking_number,
        name=payload.name,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        discord_user_id=payload.discord_user_id,
        stage=stage,
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
        existing = session.exec(select(Person).where(Person.tracking_number == person.tracking_number)).first()
        if existing:
            raise HTTPException(status_code=409, detail="tracking_number already exists")

        session.add(person)
        session.commit()
        session.refresh(person)
        return person


@router.post("/discord/upsert", response_model=Person)
def upsert_from_discord(payload: DiscordUpsert) -> Person:
    with get_session() as session:
        p = session.exec(select(Person).where(Person.discord_user_id == payload.discord_user_id)).first()

        if not p:
            p = Person(
                tracking_number=_ensure_tracking_number(payload.discord_user_id),
                name=payload.name,
                email=str(payload.email) if payload.email else None,
                phone=payload.phone,
                discord_user_id=payload.discord_user_id,
                stage=VolunteerStage.NEW,
                stage_locked=False,
                stage_last_changed_at=utcnow(),
                stage_changed_reason="auto:create_from_discord",
                region=payload.region,
                county=payload.county,
                city=payload.city,
                precinct=payload.precinct,
                recruited_by_person_id=payload.recruited_by_person_id,
            )
        else:
            if payload.name:
                p.name = payload.name
            if payload.email is not None:
                p.email = str(payload.email) if payload.email else None
            if payload.phone is not None:
                p.phone = payload.phone

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

        try:
            p.note_discord_seen(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                username=payload.username,
            )
        except Exception:
            pass

        session.add(p)
        session.commit()
        session.refresh(p)
        return p


@router.post("/onboard", response_model=OnboardResponse)
def onboard(payload: OnboardRequest) -> OnboardResponse:
    with get_session() as session:
        p: Optional[Person] = None
        if payload.person_id is not None:
            p = session.get(Person, payload.person_id)
        elif payload.discord_user_id:
            p = session.exec(select(Person).where(Person.discord_user_id == payload.discord_user_id)).first()

        if not p:
            raise HTTPException(status_code=404, detail="Person not found")

        if payload.email is not None:
            p.email = str(payload.email) if payload.email else None
        if payload.phone is not None:
            p.phone = payload.phone
        if payload.county is not None:
            p.county = payload.county
        if payload.city is not None:
            p.city = payload.city

        if payload.allow_discord_comms is not None:
            p.allow_discord_comms = payload.allow_discord_comms
        if payload.allow_tracking is not None:
            p.allow_tracking = payload.allow_tracking

        try:
            p.mark_onboarded()
        except Exception:
            pass

        try:
            p.note_discord_seen(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                username=payload.username,
            )
        except Exception:
            pass

        if p.stage == VolunteerStage.OBSERVER and not bool(getattr(p, "stage_locked", False)):
            apply_stage_change(
                session=session,
                person=p,
                new_stage=VolunteerStage.NEW,
                reason="onboard:observer->new",
                lock_stage=False,
            )
            return OnboardResponse(person=_person_to_dict(p), next_steps=_next_steps_for_person(p))

        session.add(p)
        session.commit()
        session.refresh(p)
        return OnboardResponse(person=_person_to_dict(p), next_steps=_next_steps_for_person(p))


@router.get("/", response_model=List[Person])
def list_people(
    limit: int = 200,
    offset: int = 0,
    stage: Optional[VolunteerStage] = None,
    county: Optional[str] = None,
    discord_user_id: Optional[str] = None,
) -> List[Person]:
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


# IMPORTANT: put static routes BEFORE /{person_id} to avoid 422 from int casting
@router.get("/by_tracking/{tracking_number}", response_model=Person)
def get_person_by_tracking(tracking_number: str) -> Person:
    with get_session() as session:
        p = session.exec(select(Person).where(Person.tracking_number == tracking_number)).first()
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        return p


@router.get("/{person_id}", response_model=Person)
def get_person(person_id: int) -> Person:
    with get_session() as session:
        p = session.get(Person, person_id)
        if not p:
            raise HTTPException(status_code=404, detail="Person not found")
        return p


@router.patch("/{person_id}", response_model=Person)
def patch_person(person_id: int, payload: PersonPatch) -> Person:
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

        if payload.name is not None:
            p.name = payload.name
        if payload.email is not None:
            p.email = str(payload.email) if payload.email else None
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

        # Access updates (canonical + legacy aliases)
        if bool(getattr(p, "stage_locked", False)) and (
            payload.team_access is not None
            or payload.fundraising_access is not None
            or payload.leader_access is not None
            or payload.team is not None
            or payload.fundraising is not None
            or payload.leader is not None
        ):
            raise HTTPException(status_code=403, detail="Access flags are managed by approvals while stage is locked.")

        team_access = payload.team_access if payload.team_access is not None else payload.team
        fundraising_access = payload.fundraising_access if payload.fundraising_access is not None else payload.fundraising
        leader_access = payload.leader_access if payload.leader_access is not None else payload.leader

        _apply_access_updates(
            p,
            team_access=team_access,
            fundraising_access=fundraising_access,
            leader_access=leader_access,
        )

        session.add(p)
        session.commit()
        session.refresh(p)
        return p


@router.put("/{person_id}", response_model=Person)
def replace_person(person_id: int, payload: PersonReplace) -> Person:
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

        if bool(getattr(p, "stage_locked", False)) and (
            payload.team_access is not None
            or payload.fundraising_access is not None
            or payload.leader_access is not None
            or payload.team is not None
            or payload.fundraising is not None
            or payload.leader is not None
        ):
            raise HTTPException(status_code=403, detail="Access flags are managed by approvals while stage is locked.")

        team_access = payload.team_access if payload.team_access is not None else payload.team
        fundraising_access = payload.fundraising_access if payload.fundraising_access is not None else payload.fundraising
        leader_access = payload.leader_access if payload.leader_access is not None else payload.leader

        _apply_access_updates(
            p,
            team_access=team_access,
            fundraising_access=fundraising_access,
            leader_access=leader_access,
        )

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

        # Import lazily so missing/renamed module doesn't crash API startup.
        try:
            from ..services.impact_engine import compute_impact  # type: ignore
        except Exception:
            raise HTTPException(status_code=501, detail="Impact engine not available in this build")

        summary = compute_impact(session, person_id)
        # summary may be a dataclass; normalize to dict
        return summary.__dict__ if hasattr(summary, "__dict__") else {"summary": summary}
