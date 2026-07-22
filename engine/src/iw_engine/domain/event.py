"""Event — an immutable, point-in-time occurrence on an entity.

The third storage shape (property / fact / event, DESIGN §2.1). An occurrence is an
inline Event when nothing needs to point at it; it is PROMOTED to a node (ChangeEvent,
Alert) when other things reference it (I.3). Events are append-only, never mutated.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import FactState, Source


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    entity_ref: str            # NodeId the occurrence happened to
    type: str                  # per-node-type vocab: OOMKilled, rollout_complete, config_changed, failover…
    occurred_at: datetime      # when it happened in the world
    observed_at: datetime      # when we recorded it
    payload: dict = Field(default_factory=dict)   # exit_code, old->new image, actor, ticket_id…
    source: Source
    source_native_name: str | None = None         # the vendor's own reason before dictionary
                                                   # canonicalization (P2 §2.3); None = LLM-authored
    # lifecycle — symmetric with Fact (VALIDATION-VERDICT §B P0 #2). A point-in-time occurrence
    # cannot be superseded (no window to close), but a wrong telemetry Event (flaky exporter,
    # misattributed occurrence) is RETRACTED — tombstoned via state + invalidated_by, never deleted.
    state: FactState = FactState.ACTIVE
    invalidated_by: str | None = None             # id of what proved this occurrence wrong
    # P3 airlock (DOMAIN-v3 §2.4): an occurrence whose type the dictionary does not know lands
    # quarantined (`x.<source>.<native>`) + provisional — journaled and counted, never erased.
    provisional: bool = False
    created_by: int            # journal seq — lineage
