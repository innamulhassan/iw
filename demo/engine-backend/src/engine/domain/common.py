"""Shared base + small value types used across the domain model."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    """Strict base: forbid unknown fields (so the model stays faithful to the design — a typo or a
    dropped/renamed field fails loudly), and allow population by field name as well as alias."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Confidence(Base):
    """Confidence carries its WHY, never a bare number. 04-data-model §3.4 / §4.4."""

    value: float = Field(ge=0.0, le=1.0)
    basis: str
