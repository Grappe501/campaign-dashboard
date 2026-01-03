from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import event
from sqlmodel import Field, SQLModel


def _utcnow_naive() -> datetime:
    # Keep timestamps UTC-naive for consistency across SQLite + existing models.
    return datetime.utcnow().replace(tzinfo=None)


def _default_expires_at() -> datetime:
    return _utcnow_naive() + timedelta(days=7)


def _require_positive_int(value: object, field: str) -> int:
    try:
        i = int(value)  # type: ignore[arg-type]
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if i < 1:
        raise ValueError(f"{field} must be a positive integer")
    return i


def _normalize_channel(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if s in ("email", "e-mail"):
        return "email"
    if s in ("sms", "text"):
        return "sms"
    if s in ("discord", "dm"):
        return "discord"
    return s or "discord"


def _clean_destination(raw: Optional[str], max_len: int = 200) -> str:
    # Keep permissive but non-empty; clamp to avoid accidental huge payloads.
    s = (raw or "").strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _clean_token_hash(raw: Optional[str], max_len: int = 256) -> str:
    # We store a hash (sha256 hex is 64 chars), but keep tolerant if algo changes.
    s = (raw or "").strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


class Power5Invite(SQLModel, table=True):
    """
    Magic-link onboarding stub (email/sms/discord).
    Store only a token hash; return the raw token once at creation time.
    """

    __tablename__ = "power5_invites"

    id: Optional[int] = Field(default=None, primary_key=True)

    power_team_id: int = Field(foreign_key="power_teams.id", index=True)
    invited_by_person_id: int = Field(foreign_key="people.id", index=True)

    invitee_person_id: Optional[int] = Field(default=None, foreign_key="people.id", index=True)

    # email | sms | discord
    channel: str = Field(index=True)
    destination: str = Field(index=True)

    token_hash: str = Field(index=True, unique=True)

    expires_at: datetime = Field(default_factory=_default_expires_at, index=True)
    consumed_at: Optional[datetime] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=_utcnow_naive, index=True)

    # Convenience helpers (safe)
    def is_expired(self) -> bool:
        try:
            return bool(self.expires_at and self.expires_at < _utcnow_naive())
        except Exception:
            return False

    def is_consumed(self) -> bool:
        return self.consumed_at is not None


# --- Validate/normalize before writes ---


@event.listens_for(Power5Invite, "before_insert")
def _power5invite_before_insert(mapper, connection, target) -> None:  # noqa: ANN001
    # Validate FKs (fail closed)
    target.power_team_id = _require_positive_int(getattr(target, "power_team_id", None), "power_team_id")
    target.invited_by_person_id = _require_positive_int(
        getattr(target, "invited_by_person_id", None),
        "invited_by_person_id",
    )
    if getattr(target, "invitee_person_id", None) is not None:
        target.invitee_person_id = _require_positive_int(
            getattr(target, "invitee_person_id", None),
            "invitee_person_id",
        )

    # Normalize channel/destination/token_hash
    target.channel = _normalize_channel(getattr(target, "channel", None))
    target.destination = _clean_destination(getattr(target, "destination", None))
    if not target.destination:
        raise ValueError("Power5Invite.destination must not be empty.")

    th = _clean_token_hash(getattr(target, "token_hash", None))
    if not th:
        raise ValueError("Power5Invite.token_hash must not be empty.")
    target.token_hash = th

    now = _utcnow_naive()

    # Ensure created_at
    if not getattr(target, "created_at", None):
        target.created_at = now

    # Ensure expires_at is present and sane
    if not getattr(target, "expires_at", None):
        target.expires_at = _default_expires_at()

    # Guard against misconfigured expires_at
    try:
        if target.expires_at < target.created_at:
            # If operator manually set a bad expires_at, fail closed.
            raise ValueError("Power5Invite.expires_at must be >= created_at.")
    except Exception:
        # If comparison fails, fail closed.
        raise

    # consumed_at stays None unless explicitly set


@event.listens_for(Power5Invite, "before_update")
def _power5invite_before_update(mapper, connection, target) -> None:  # noqa: ANN001
    # Keep normalization stable on updates too
    if hasattr(target, "channel"):
        target.channel = _normalize_channel(getattr(target, "channel", None))

    if hasattr(target, "destination"):
        target.destination = _clean_destination(getattr(target, "destination", None))
        if not getattr(target, "destination", ""):
            raise ValueError("Power5Invite.destination must not be empty.")

    if hasattr(target, "token_hash"):
        th = _clean_token_hash(getattr(target, "token_hash", None))
        if not th:
            raise ValueError("Power5Invite.token_hash must not be empty.")
        target.token_hash = th

    # If consumed_at is present, it must be datetime or None
    if hasattr(target, "consumed_at"):
        ca = getattr(target, "consumed_at", None)
        if ca is not None and not isinstance(ca, datetime):
            raise ValueError("Power5Invite.consumed_at must be a datetime or None.")

    # If expires_at is present, keep it sane relative to created_at (if both exist)
    if hasattr(target, "expires_at") and getattr(target, "expires_at", None) is not None:
        exp = getattr(target, "expires_at", None)
        created = getattr(target, "created_at", None)
        if isinstance(exp, datetime) and isinstance(created, datetime) and exp < created:
            raise ValueError("Power5Invite.expires_at must be >= created_at.")
