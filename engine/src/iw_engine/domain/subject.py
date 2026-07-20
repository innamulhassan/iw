"""SubjectRef — domain-neutral identity of what is under investigation.

Deliberately never a bare `incident_id`: `{domain, id}` lets a second investigation
domain (provisioning, capacity, data-quality) reuse the same engine unchanged.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SubjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str  # e.g. "app-incident"
    id: str      # e.g. "INC-4821"
    kind: str    # e.g. "incident"

    @property
    def key(self) -> str:
        return f"{self.domain}:{self.id}"
