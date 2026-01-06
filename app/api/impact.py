from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field as PydField
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.impact_action import ImpactAction, ActionChannel, ActionSource
from ..models.impact_rule import ImpactRule

router = APIRouter(prefix="/impact", tags=["impact"])


# -----------------------------------------------------------------------------
# Default rules (used by /bootstrap/rules and as safe fallback in reach summary)
# -----------------------------------------------------------------------------
DEFAULT_RULES: Dict[str, float] = {
    # Core voter contact actions
    "call": 1.0,
    "text": 0.6,
    "door": 1.5,
    # Events & community
    "event_hosted": 10.0,
    "event_attended": 3.0,
    # Digital
    "post_shared": 0.4,
    "signup": 2.0,
    # Catch-all
    "other": 0.2,
}


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class ImpactActionCreate(BaseModel):
    action_type: str = PydField(..., min_length=1, max_length=80)
    quantity: int = PydField(default=1, ge=1, le=10000)

    # Identity / attribution (optional but recommended)
    actor_person_id: Optional[int] = PydField(default=None, ge=1)
    county_id: Optional[int] = PydField(default=None, ge=1)
    power_team_id: Optional[int] = PydField(default=None, ge=1)

    # Dedupe retries / double submits
    idempotency_key: Optional[str] = PydField(default=None, max_length=200)

    # Optional metadata
    meta: Dict[str, Any] = PydField(default_factory=dict)

    # Optional classification (safe defaults apply if omitted)
    source: Optional[str] = None
    channel: Optional[str] = None

    # Optional occurred_at override (otherwise server timestamp)
    occurred_at: Optional[datetime] = None


class ImpactActionOut(BaseModel):
    id: int
    action_type: str
    quantity: int
    actor_person_id: Optional[int] = None
    county_id: Optional[int] = None
    power_team_id: Optional[int] = None
    idempotency_key: Optional[str] = None
    source: str
    channel: str
    occurred_at: datetime
    created_at: datetime
    meta: Dict[str, Any]


class ImpactReachSummaryOut(BaseModel):
    computed_reach: float
    actions_total: int
    rules_loaded: int
    quantity_by_type: Dict[str, int]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _coerce_source(raw: Optional[str]) -> ActionSource:
    if not raw:
        return ActionSource.UNKNOWN
    s = str(raw).strip().lower()
    for v in ActionSource:
        if v.value == s:
            return v
    return ActionSource.UNKNOWN


def _coerce_channel(raw: Optional[str], action_type: str) -> ActionChannel:
    """
    Prefer explicit channel; else infer loosely from action_type.
    """
    if raw:
        s = str(raw).strip().lower()
        for v in ActionChannel:
            if v.value == s:
                return v

    at = (action_type or "").strip().lower()
    if at in ("call",):
        return ActionChannel.CALL
    if at in ("text",):
        return ActionChannel.TEXT
    if at in ("door", "knock"):
        return ActionChannel.DOOR
    if "event" in at or "meeting" in at or "rally" in at:
        return ActionChannel.EVENT
    if "post" in at or "share" in at or "social" in at:
        return ActionChannel.SOCIAL
    return ActionChannel.OTHER


def _summary_filters(
    start: Optional[datetime],
    end: Optional[datetime],
    actor_person_id: Optional[int],
    power_team_id: Optional[int],
    county_id: Optional[int],
) -> Tuple[list, Dict[str, Any]]:
    """
    Returns (where_clauses, debug_meta)
    """
    where = []
    meta: Dict[str, Any] = {}

    if start:
        where.append(ImpactAction.occurred_at >= start)
        meta["start"] = start.isoformat()
    if end:
        where.append(ImpactAction.occurred_at < end)
        meta["end"] = end.isoformat()
    if actor_person_id:
        where.append(ImpactAction.actor_person_id == actor_person_id)
        meta["actor_person_id"] = actor_person_id
    if power_team_id:
        where.append(ImpactAction.power_team_id == power_team_id)
        meta["power_team_id"] = power_team_id
    if county_id:
        where.append(ImpactAction.county_id == county_id)
        meta["county_id"] = county_id

    return where, meta


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.post("/actions", response_model=ImpactActionOut)
def create_action(payload: ImpactActionCreate) -> ImpactActionOut:
    """
    Create an ImpactAction row (idempotent if idempotency_key provided).

    Used by:
      - Discord /log command (via bot -> API)
      - Wins automation (best-effort autolog)
    """
    action_type = (payload.action_type or "").strip()
    if not action_type:
        raise HTTPException(status_code=400, detail="action_type is required")

    # Clamp quantity defensively even though schema guards
    qty = int(payload.quantity or 1)
    if qty < 1:
        qty = 1
    if qty > 10000:
        qty = 10000

    with get_session() as session:
        # Idempotency: return existing row if key already used
        if payload.idempotency_key:
            existing = session.exec(
                select(ImpactAction).where(ImpactAction.idempotency_key == payload.idempotency_key)
            ).first()
            if existing:
                return ImpactActionOut(
                    id=int(existing.id),  # type: ignore[arg-type]
                    action_type=existing.action_type,
                    quantity=int(existing.quantity),
                    actor_person_id=existing.actor_person_id,
                    county_id=existing.county_id,
                    power_team_id=existing.power_team_id,
                    idempotency_key=existing.idempotency_key,
                    source=str(existing.source.value if hasattr(existing.source, "value") else existing.source),
                    channel=str(existing.channel.value if hasattr(existing.channel, "value") else existing.channel),
                    occurred_at=existing.occurred_at,
                    created_at=existing.created_at,
                    meta=existing.meta or {},
                )

        source = _coerce_source(payload.source)
        channel = _coerce_channel(payload.channel, action_type)

        occurred_at = payload.occurred_at or None

        row = ImpactAction(
            actor_person_id=payload.actor_person_id,
            county_id=payload.county_id,
            power_team_id=payload.power_team_id,
            action_type=action_type,
            quantity=qty,
            source=source,
            channel=channel,
            idempotency_key=(payload.idempotency_key.strip() if payload.idempotency_key else None),
            occurred_at=occurred_at or None,  # model default applies if None
            meta=payload.meta or {},
        )

        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

            # Race-safe idempotency fallback:
            if payload.idempotency_key:
                existing = session.exec(
                    select(ImpactAction).where(ImpactAction.idempotency_key == payload.idempotency_key)
                ).first()
                if existing:
                    return ImpactActionOut(
                        id=int(existing.id),  # type: ignore[arg-type]
                        action_type=existing.action_type,
                        quantity=int(existing.quantity),
                        actor_person_id=existing.actor_person_id,
                        county_id=existing.county_id,
                        power_team_id=existing.power_team_id,
                        idempotency_key=existing.idempotency_key,
                        source=str(existing.source.value if hasattr(existing.source, "value") else existing.source),
                        channel=str(existing.channel.value if hasattr(existing.channel, "value") else existing.channel),
                        occurred_at=existing.occurred_at,
                        created_at=existing.created_at,
                        meta=existing.meta or {},
                    )

            raise HTTPException(status_code=409, detail="Could not create impact action (integrity error).")

        session.refresh(row)

        return ImpactActionOut(
            id=int(row.id),  # type: ignore[arg-type]
            action_type=row.action_type,
            quantity=int(row.quantity),
            actor_person_id=row.actor_person_id,
            county_id=row.county_id,
            power_team_id=row.power_team_id,
            idempotency_key=row.idempotency_key,
            source=str(row.source.value if hasattr(row.source, "value") else row.source),
            channel=str(row.channel.value if hasattr(row.channel, "value") else row.channel),
            occurred_at=row.occurred_at,
            created_at=row.created_at,
            meta=row.meta or {},
        )


@router.get("/reach/summary", response_model=ImpactReachSummaryOut)
def reach_summary(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    actor_person_id: Optional[int] = None,
    power_team_id: Optional[int] = None,
    county_id: Optional[int] = None,
) -> ImpactReachSummaryOut:
    """
    Compute reach = sum(quantity * reach_per_unit(rule[action_type])) over filtered actions.

    Response keys are aligned with Discord command expectations:
      - computed_reach
      - actions_total
      - rules_loaded
      - quantity_by_type
    """
    with get_session() as session:
        # Load rules from DB; fall back to DEFAULT_RULES; final fallback is 1.0
        rules = {r.action_type: float(r.reach_per_unit) for r in session.exec(select(ImpactRule)).all()}
        rules_loaded = len(rules)

        where, _meta = _summary_filters(start, end, actor_person_id, power_team_id, county_id)

        q = select(ImpactAction)
        for clause in where:
            q = q.where(clause)

        actions = session.exec(q).all()

        qty_by_type: Dict[str, int] = {}
        computed = 0.0

        for a in actions:
            at = (a.action_type or "other").strip()
            qty = int(a.quantity or 0)
            if qty < 0:
                qty = 0

            qty_by_type[at] = qty_by_type.get(at, 0) + qty

            r = rules.get(at)
            if r is None:
                r = DEFAULT_RULES.get(at, DEFAULT_RULES.get("other", 1.0))

            computed += float(qty) * float(r)

        return ImpactReachSummaryOut(
            computed_reach=round(computed, 4),
            actions_total=len(actions),
            rules_loaded=rules_loaded,
            quantity_by_type=qty_by_type,
        )
