"""SubjectRef — domain-neutral subject identity. 04-data-model §2.

Used by PhaseRecord (§4.1), Feedback (§5), the run, and the read-model (§8). The engine never
assumes the word "incident": the unique key is (domain, id).
"""
from __future__ import annotations

from .common import Base


class SubjectRef(Base):
    domain: str  # = Playbook.domain — which world this is (app-incident, provisioning, …)
    id: str      # the subject's identity IN that domain (INC-4821, REQ-123)
    kind: str    # the subject node's kind (incident, provision_request)

    @property
    def key(self) -> tuple[str, str]:
        """The global subject identity — unique across the engine."""
        return (self.domain, self.id)
