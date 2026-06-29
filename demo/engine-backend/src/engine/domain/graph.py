"""The per-investigation world: Node, Fact, Edge. 04-data-model §3.

These are the persisted shapes. The live, traversable graph (networkx) is built over them in P2
(graph_runtime). `kind`/`type`/`layer`/edge-type/fact-key value-spaces come from the loaded
playbook's `graph_schema`; here they are typed loosely (str) so the same shapes serve any domain,
with incident-triage's enums available in `enums` for validation where the engine wants it.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from .common import Base
from .enums import ImpactState


class Fact(Base):
    """An observation on a node. 04-data-model §3.3. NO ttl — freshness is judged at read time
    against what the reader needs."""

    key: str                                  # fact key from the playbook (health|error_rate|…)
    value: Any
    source: str                               # the tool that observed it
    evidence_ref: Optional[str] = None        # deep link to backing data — auditable
    observed_at: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    impact_state: Optional[ImpactState] = None  # erroring|degraded|stalled|lagging|wrong-data|ok


class Node(Base):
    """A thing in the investigation. 04-data-model §3.1. `id` is `kind:name` — stable; everything
    refers to a node by id."""

    id: str
    kind: str                                 # from playbook node_types (system|incident|change|alert)
    type: str = "generic"                     # a type within the kind
    layer: Optional[str] = None               # system only: business|app|database|network|…
    name: Optional[str] = None
    labels: list[str] = Field(default_factory=list)   # suspect|policy_block|central drive triage
    props: dict[str, Any] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)
    summary: Optional[str] = None
    sources: list[str] = Field(default_factory=list)  # which tools contributed this node


class Edge(Base):
    """A typed relationship. 04-data-model §3.4. Direction matters: blast-radius and cause-finding
    walk opposite ways. `props` is open (carries confidence{value,basis}, rank, path, …)."""

    type: str                                 # edge type from the playbook (suspected_cause, …)
    from_: str = Field(alias="from")          # `from` is a Python keyword → alias
    to: str
    props: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
