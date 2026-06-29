"""The read-model — one denormalized incident document per subject. 04-data-model §8 / D6.

The UI + reads hit this (Mongo in production; in-memory here). It is projected from the run state
(phase records) + the investigation graph after each step — the structured truth a client renders
as a snapshot, then applies live channel deltas on top.
"""
from __future__ import annotations

from typing import Optional

from engine.graph_runtime import IncidentGraph


class ReadModelStore:
    """Keyed by (domain, id) — the global subject identity (SubjectRef)."""

    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], dict] = {}

    def upsert(self, doc: dict) -> None:
        self._docs[(doc["domain"], doc["_id"])] = doc

    def get(self, domain: str, subject_id: str) -> Optional[dict]:
        return self._docs.get((domain, subject_id))


def project_incident(subject: dict, values: dict, graph: IncidentGraph, *, paused: bool,
                     terminal: Optional[str] = None) -> dict:
    """Project the run state + graph into the incident document (the read-model). `terminal` (e.g.
    'denied') overrides the derived state for a halted run so polling clients aren't misled into
    thinking approval is still pending."""
    records = values.get("phase_records", [])
    assess = next((r.get("output") or {} for r in records
                   if r.get("phase") == "assess" and r.get("output")), {})
    state = terminal or ("waiting_approval" if paused else ("closed" if records else "triage"))
    return {
        "_id": subject["id"],
        "domain": subject["domain"],
        "subject": subject,
        "state": state,
        "symptom": assess.get("symptom"),
        "impact_assessment": assess.get("impact_assessment"),
        "phases": [{"id": r.get("id"), "phase": r.get("phase"), "state": r.get("state")}
                   for r in records],
        "graph": {"node_count": len(graph), "nodes": graph.node_ids()},
    }
