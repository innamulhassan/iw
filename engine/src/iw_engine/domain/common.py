"""Shared value objects — Confidence, EvidenceRef, id aliases.

`Confidence{value, basis}` (basis MANDATORY) is the resolved numeric belief; the LLM
emits a coarse `ConfidenceLevel` rubric (enums.py) which the ledger maps to a value
using the playbook's tunable band map (DESIGN §2.3 R-C4). A directly-*measured* fact
carries `source_reliability` instead of a belief `Confidence`.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# id aliases — kept as plain str so refs are cheap + serialisable
NodeId = str
EdgeId = str
FactId = str
EventId = str
Seq = int  # monotonic journal sequence — the event-sourcing spine


class Confidence(BaseModel):
    """A belief with a MANDATORY basis — never a naked float (principle 10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float = Field(ge=0.0, le=1.0)
    basis: str = Field(min_length=1)


class EvidenceRef(BaseModel):
    """A pointer to the raw proof behind a fact — a metric query, trace id, log link,
    snapshot, diff, blame line, CI record — so every claim is reconstructable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str  # metric_query | trace_id | log_link | snapshot | diff | blame | ci_record | url
    ref: str
    label: str | None = None
