from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Keep timestamps UTC-naive for consistent storage/ordering in SQLite.
    return datetime.utcnow().replace(tzinfo=None)


def _clean_slug(raw: Optional[str], max_len: int = 80) -> str:
    s = (raw or "").strip().lower()
    if not s:
        raise ValueError("slug is required")
    # Safe-ish slug normalization: keep letters/numbers/dash/underscore.
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    slug = "".join(out).strip("-")
    if not slug:
        raise ValueError("slug is required")
    if len(slug) > max_len:
        slug = slug[:max_len]
    return slug


def _clean_title(raw: Optional[str], max_len: int = 140) -> str:
    s = (raw or "").strip()
    if not s:
        raise ValueError("title is required")
    if len(s) > max_len:
        s = s[:max_len]
    return s


class TrainingModule(SQLModel, table=True):
    """
    A training item volunteers can complete.
    Served by /training/modules and referenced by /training/complete.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # Human-friendly stable identifier for Discord UX and future imports
    slug: str = Field(index=True, unique=True, max_length=80)

    title: str = Field(max_length=140)
    description: Optional[str] = Field(default=None, max_length=1000)

    # Ordering for lists
    sort_order: int = Field(default=100, index=True)

    # Optional time estimate for volunteers
    estimated_minutes: Optional[int] = Field(default=None)

    is_active: bool = Field(default=True, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    def validate(self) -> None:
        self.slug = _clean_slug(self.slug)
        self.title = _clean_title(self.title)

        if self.description is not None:
            d = self.description.strip()
            self.description = d[:1000] if len(d) > 1000 else d

        if self.sort_order < 0:
            raise ValueError("sort_order must be >= 0")

        if self.estimated_minutes is not None:
            try:
                m = int(self.estimated_minutes)
            except Exception:
                raise ValueError("estimated_minutes must be an integer")
            if m < 1:
                raise ValueError("estimated_minutes must be a positive integer")
            self.estimated_minutes = m
