from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class ImpactRule(SQLModel, table=True):
    """
    Tuning knobs: how many 'reach points' each action produces per unit.
    """
    __tablename__ = "impact_rules"

    id: Optional[int] = Field(default=None, primary_key=True)

    action_type: str = Field(index=True, unique=True)
    reach_per_unit: float = Field(default=1.0)

    notes: Optional[str] = Field(default=None)

    updated_at: datetime = Field(default_factory=datetime.utcnow)
