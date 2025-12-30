from __future__ import annotations

from datetime import datetime, date, timezone
from typing import Optional, Dict, Any, Tuple, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func
from sqlmodel import select

from ..database import get_session
from ..models.impact_action import ImpactAction, ActionSource, ActionChannel
from ..models.impact_rule import ImpactRule
from ..models.impact_reach_snapshot import ImpactReachSnapshot
from ..models.person import Person, VolunteerStage
from ..services.stage_engine import (
    PersonImpactStats,
    evaluate_stage_change_from_impact,
    apply_stage_change,
)

router = APIRouter(prefix="/impact", tags=["impact"])


def utcnow() -> datetime:
    # Keep UTC-aware now for API payloads
    return datetime.now(timezone.utc)


DEFAULT_RULES: Dict[str, float] = {
    "call": 1.2,
    "text": 0.6,
    "door": 1.8,
    "event_hosted": 35.0,
    "event_attended": 8.0,
    "post_shared": 25.0,
    "signup": 5.0,
}


# -----------------------------
# Schemas (do NOT use DB model as input)
# -----------------------------

class ImpactActionCreate(BaseModel):
    """
    Client input for creating an ImpactAction.

    NOTE:
    - DB-only fields are not accepted (id, created_at, etc).
    - created_at is always set server-side.
    - occurred_at defaults to "now" if omitted.
    """
    action_type: str = PydField(..., min_length=1)
    quantity: int = 1

    actor_person_id: Optional[int] = None
    county_id: Optional[int] = None
    power_team_id: Optional[int] = None

    occurred_at: Optional[datetime] = None

    source: Optional[ActionSource] = None
    channel: Optional[ActionChannel] = None

    idempotency_key: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class ImpactActionOut(BaseModel):
    """
    Response payload for create_action.
    """
    # core action fields
    id: int
    action_type: str
    quantity: int
    actor_person_id: Optional[int] = None
    county_id: Optional[int] = None
    power_team_id: Optional[int] = None
    occurred_at: datetime
    created_at: datetime
    source: ActionSource
    channel: ActionChannel
    idempotency_key: Optional[str] = None
    meta: Dict[str, Any]

    # extras
    deduped: bool
    stage_changed_to: Optional[str] = None
    actor_stage: Optional[str] = None


# -----------------------------
# Stage auto-promotion (safe, early phases only)
# -----------------------------

_EARLY_STAGES = {
    VolunteerStage.OBSERVER,
    VolunteerStage.NEW,
    VolunteerStage.ACTIVE,
    VolunteerStage.OWNER,
}

_GATED_STAGES = {
    VolunteerStage.TEAM,
    VolunteerStage.FUNDRAISING,
    VolunteerStage.LEADER,
    # Keep ADMIN gated (if your enum contains it). If not, no harm.
    getattr(VolunteerStage, "ADMIN", VolunteerStage.LEADER),
}


def _get_actor_action_row_count(session, actor_person_id: int) -> int:
    """
    Count total ImpactAction rows for this actor.
    Note: this is "rows", not "quantity sum". That's intentional for early arc gating.
    """
    return int(
        session.exec(
            select(func.count()).select_from(ImpactAction).where(ImpactAction.actor_person_id == actor_person_id)
        ).one()
    )


def _safe_stage_str(p: Optional[Person]) -> Optional[str]:
    if not p:
        return None
    st = getattr(p, "stage", None)
    return str(st) if st is not None else None


def _try_auto_promote_actor(session, actor_person_id: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """
    Attempts auto stage promotion for the actor only in early phases.

    Returns: (stage_changed_to, actor_stage_after)
      - stage_changed_to: new stage if changed, else None
      - actor_stage_after: current stage (string) if person exists, else None

    This function is defensive and will never block action logging.
    """
    if actor_person_id is None:
        return None, None

    person = session.get(Person, actor_person_id)
    if not person:
        return None, None

    # If locked, do not auto-promote.
    if bool(getattr(person, "stage_locked", False)):
        return None, _safe_stage_str(person)

    cur_stage = getattr(person, "stage", None)
    if cur_stage is None:
        return None, None

    # If already gated, never auto-promote
    if cur_stage in _GATED_STAGES:
        return None, _safe_stage_str(person)

    # Only run auto-promotion in early arc
    if cur_stage not in _EARLY_STAGES:
        return None, _safe_stage_str(person)

    cur_stage_str = str(cur_stage)

    try:
        total_rows = _get_actor_action_row_count(session, actor_person_id)
        decision = evaluate_stage_change_from_impact(person, PersonImpactStats(actions_total=total_rows))
        if not decision.should_change or not decision.new_stage:
            return None, cur_stage_str

        # Belt/suspenders: never promote into gated stages here
        if decision.new_stage in _GATED_STAGES:
            return None, cur_stage_str

        apply_stage_change(
            session=session,
            person=person,
            new_stage=decision.new_stage,
            reason=decision.reason or f"auto:{cur_stage_str}->{str(decision.new_stage)}",
            lock_stage=False,
        )
        return str(decision.new_stage), _safe_stage_str(person)
    except Exception:
        # Do not block action logging if promotion fails
        try:
            session.rollback()
        except Exception:
            pass
        return None, cur_stage_str


# -----------------------------
# Actions
# -----------------------------

def _clamp_quantity(q: Any) -> int:
    try:
        qty = int(q or 1)
    except Exception:
        qty = 1
    if qty < 1:
        qty = 1
    if qty > 10000:
        qty = 10000
    return qty


@router.post("/actions", response_model=ImpactActionOut)
def create_action(payload: ImpactActionCreate) -> ImpactActionOut:
    """
    Create an impact action (Discord/web/etc).

    Enhancements:
      - idempotency_key supported: if present and already exists, returns existing row.
      - attempts auto stage promotion for early phases (observer/new -> active, active -> owner)
      - returns extra fields: deduped, stage_changed_to, actor_stage
    """
    qty = _clamp_quantity(payload.quantity)

    with get_session() as session:
        # 1) Dedupe if idempotency_key provided
        if payload.idempotency_key:
            existing = session.exec(
                select(ImpactAction).where(ImpactAction.idempotency_key == payload.idempotency_key)
            ).first()
            if existing:
                actor_stage = (
                    _safe_stage_str(session.get(Person, existing.actor_person_id))
                    if existing.actor_person_id
                    else None
                )
                return ImpactActionOut(
                    id=existing.id,
                    action_type=existing.action_type,
                    quantity=int(existing.quantity),
                    actor_person_id=existing.actor_person_id,
                    county_id=existing.county_id,
                    power_team_id=existing.power_team_id,
                    occurred_at=existing.occurred_at,
                    created_at=existing.created_at,
                    source=existing.source,
                    channel=existing.channel,
                    idempotency_key=existing.idempotency_key,
                    meta=existing.meta or {},
                    deduped=True,
                    stage_changed_to=None,
                    actor_stage=actor_stage,
                )

        # 2) Build DB model from schema (server owns defaults)
        action = ImpactAction(
            action_type=payload.action_type,
            quantity=qty,
            actor_person_id=payload.actor_person_id,
            county_id=payload.county_id,
            power_team_id=payload.power_team_id,
            occurred_at=payload.occurred_at or utcnow(),
            created_at=utcnow(),
            source=payload.source or ActionSource.UNKNOWN,
            channel=payload.channel or ActionChannel.OTHER,
            idempotency_key=payload.idempotency_key,
            meta=payload.meta or {},
        )

        # 3) Persist
        session.add(action)
        session.commit()
        session.refresh(action)

        # 4) Auto-promote actor (defensive + safe-guarded)
        stage_changed_to, actor_stage = _try_auto_promote_actor(session, action.actor_person_id)

        # Ensure actor_stage returned even if no promotion happened
        if actor_stage is None and action.actor_person_id:
            actor_stage = _safe_stage_str(session.get(Person, action.actor_person_id))

        return ImpactActionOut(
            id=action.id,
            action_type=action.action_type,
            quantity=int(action.quantity),
            actor_person_id=action.actor_person_id,
            county_id=action.county_id,
            power_team_id=action.power_team_id,
            occurred_at=action.occurred_at,
            created_at=action.created_at,
            source=action.source,
            channel=action.channel,
            idempotency_key=action.idempotency_key,
            meta=action.meta or {},
            deduped=False,
            stage_changed_to=stage_changed_to,
            actor_stage=actor_stage,
        )


@router.get("/actions", response_model=list[ImpactAction])
def list_actions(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    county_id: Optional[int] = None,
    power_team_id: Optional[int] = None,
    actor_person_id: Optional[int] = None,
    action_type: Optional[str] = None,
) -> list[ImpactAction]:
    with get_session() as session:
        q = select(ImpactAction)
        if start:
            q = q.where(ImpactAction.occurred_at >= start)
        if end:
            q = q.where(ImpactAction.occurred_at < end)
        if county_id:
            q = q.where(ImpactAction.county_id == county_id)
        if power_team_id:
            q = q.where(ImpactAction.power_team_id == power_team_id)
        if actor_person_id:
            q = q.where(ImpactAction.actor_person_id == actor_person_id)
        if action_type:
            q = q.where(ImpactAction.action_type == action_type)

        # Keep ordering stable for older DBs: occurred_at is expected, but id always exists.
        q = q.order_by(ImpactAction.occurred_at.desc(), ImpactAction.id.desc())
        return list(session.exec(q).all())


# -----------------------------
# Rules
# -----------------------------

@router.get("/rules", response_model=list[ImpactRule])
def list_rules() -> list[ImpactRule]:
    with get_session() as session:
        return list(session.exec(select(ImpactRule)).all())


@router.post("/rules/bootstrap")
def bootstrap_rules() -> Dict[str, Any]:
    """
    Idempotently create common rules if missing.
    """
    with get_session() as session:
        existing = {r.action_type: r for r in session.exec(select(ImpactRule)).all()}
        created = 0
        for k, v in DEFAULT_RULES.items():
            if k in existing:
                continue
            session.add(ImpactRule(action_type=k, reach_per_unit=v, notes="bootstrap default"))
            created += 1
        session.commit()
        return {"created": created, "defaults": DEFAULT_RULES}


@router.put("/rules/{action_type}", response_model=ImpactRule)
def upsert_rule(action_type: str, reach_per_unit: float, notes: Optional[str] = None) -> ImpactRule:
    with get_session() as session:
        r = session.exec(select(ImpactRule).where(ImpactRule.action_type == action_type)).first()
        if r:
            r.reach_per_unit = reach_per_unit
            r.notes = notes
            # Prefer consistent UTC-aware time where possible
            r.updated_at = utcnow()
            session.add(r)
            session.commit()
            session.refresh(r)
            return r

        r = ImpactRule(action_type=action_type, reach_per_unit=reach_per_unit, notes=notes)
        session.add(r)
        session.commit()
        session.refresh(r)
        return r


# -----------------------------
# Reach summary + snapshots
# -----------------------------

@router.get("/reach/summary")
def reach_summary(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    county_id: Optional[int] = None,
    power_team_id: Optional[int] = None,
    actor_person_id: Optional[int] = None,
) -> Dict[str, Any]:
    with get_session() as session:
        rules = {r.action_type: float(r.reach_per_unit) for r in session.exec(select(ImpactRule)).all()}

        q = select(ImpactAction)
        if start:
            q = q.where(ImpactAction.occurred_at >= start)
        if end:
            q = q.where(ImpactAction.occurred_at < end)
        if county_id:
            q = q.where(ImpactAction.county_id == county_id)
        if power_team_id:
            q = q.where(ImpactAction.power_team_id == power_team_id)
        if actor_person_id:
            q = q.where(ImpactAction.actor_person_id == actor_person_id)

        actions = list(session.exec(q).all())

        by_type: Dict[str, int] = {}
        reach = 0.0
        for a in actions:
            by_type[a.action_type] = by_type.get(a.action_type, 0) + int(a.quantity)
            reach += float(a.quantity) * float(rules.get(a.action_type, 1.0))

        return {
            "filters": {
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "county_id": county_id,
                "power_team_id": power_team_id,
                "actor_person_id": actor_person_id,
            },
            "actions_total": len(actions),  # rows
            "quantity_by_type": by_type,
            "computed_reach": round(reach, 3),
            "rules_loaded": len(rules),
        }


@router.post("/reach/recompute")
def recompute_snapshot(
    period_start: date,
    period_end: date,
    group_by: str = "none",  # none|county|team|person
) -> Dict[str, Any]:
    if group_by not in ("none", "county", "team", "person"):
        raise HTTPException(status_code=400, detail="group_by must be one of: none|county|team|person")

    with get_session() as session:
        rules = {r.action_type: float(r.reach_per_unit) for r in session.exec(select(ImpactRule)).all()}

        # Use UTC-aware boundaries to match utcnow()-style timestamps
        start_dt = datetime.combine(period_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(period_end, datetime.min.time(), tzinfo=timezone.utc)

        q = select(ImpactAction).where(ImpactAction.occurred_at >= start_dt, ImpactAction.occurred_at < end_dt)
        actions = list(session.exec(q).all())

        def key(a: ImpactAction):
            if group_by == "county":
                return ("county", a.county_id)
            if group_by == "team":
                return ("team", a.power_team_id)
            if group_by == "person":
                return ("person", a.actor_person_id)
            return ("none", None)

        buckets: Dict[tuple, list[ImpactAction]] = {}
        for a in actions:
            buckets.setdefault(key(a), []).append(a)

        created = 0
        for (mode, idval), items in buckets.items():
            computed = 0.0
            qty_total = 0
            for a in items:
                qty_total += int(a.quantity)
                computed += float(a.quantity) * float(rules.get(a.action_type, 1.0))

            snap = ImpactReachSnapshot(
                period_start=period_start,
                period_end=period_end,
                county_id=idval if mode == "county" else None,
                power_team_id=idval if mode == "team" else None,
                actor_person_id=idval if mode == "person" else None,
                computed_reach=float(computed),
                # NOTE: this field is quantity sum (legacy naming)
                actions_total=int(qty_total),
            )
            session.add(snap)
            created += 1

        session.commit()
        return {
            "created": created,
            "group_by": group_by,
            "period_start": str(period_start),
            "period_end": str(period_end),
        }
