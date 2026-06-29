"""Feedback — the operator's verdict, kept apart from the run record. 04-data-model §5.

Feeds the learning loop (similar-incident suggestions) + the autonomy ladder. Domain-neutral:
keyed by SubjectRef, never a bare incident_id.
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from .common import Base
from .enums import FeedbackKind
from .subject import SubjectRef


class Feedback(Base):
    id: Optional[str] = Field(default=None, alias="_id")
    subject: SubjectRef
    run_id: Optional[str] = None
    actor: str
    kind: FeedbackKind                        # outcome | failure | correction
    verdict: Optional[str] = None
    note: Optional[str] = None
    at: Optional[str] = None
