"""Shared value objects — Confidence, EvidenceRef, id aliases.

`Confidence{value, basis}` (basis MANDATORY) is the resolved numeric belief; the LLM
emits a coarse `ConfidenceLevel` rubric (enums.py) which the hypothesis store maps to a value
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


def enforce_belief_exclusivity(prefix: str, *, inferred: bool, inferred_desc: str,
                               measured_desc: str, confidence: object,
                               source_reliability: object) -> None:
    """R-C4 (VALIDATION-VERDICT §B P0 #3 / DOMAIN-v3 §2.2) — the ONE authoritative belief-exclusivity
    enforcer. A Fact is a VIEW over an Assertion, so this rule ran on every fact TWICE, hand-written
    two ways — `Fact` keyed on `source==llm`, `Assertion` on `channel` — that could silently drift.
    Both validators now call THIS single function, so the exactly-one-belief-field invariant has one
    home. Exactly one belief field is meaningful and WHICH one is fixed by provenance: an INFERRED
    record carries a `confidence`; a MEASURED/DECLARED/ENGINE one carries a `source_reliability` —
    never neither, never both. Each caller supplies whether it is `inferred` (Fact: source==llm;
    Assertion: channel==INFERRED — equivalent for facts) and its own `*_desc` wording so the
    diagnostic names the record in its own terms."""
    if inferred:
        if confidence is None:
            raise ValueError(f"{prefix} {inferred_desc} must carry a confidence")
        if source_reliability is not None:
            raise ValueError(f"{prefix} {inferred_desc} carries confidence, not reliability")
    else:
        if source_reliability is None:
            raise ValueError(f"{prefix} {measured_desc} must carry source_reliability")
        if confidence is not None:
            raise ValueError(
                f"{prefix} {measured_desc} carries source_reliability, not a confidence")
