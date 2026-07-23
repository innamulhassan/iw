"""The journal — append-only, human-readable, the SOURCE OF TRUTH (principle 2). Every
phase entry carries the FULL PhaseResult delta as its payload (DESIGN §2.4 R-J1), so
replaying the journal rebuilds the graph + hypothesis store exactly; `refs` is a derived index.
Persisted as NDJSON with a schema-version header; a trailing partial line is skipped on
load (crash-safety, R-J4).

SCHEMA v2 (P6 step 3, part2 §1): everything that happens in a run is one typed event log —
`kind` spans phase · gate_opened · gate_decision · message · invocation · rejection · repair ·
lifecycle (plus the v1 "step", accepted read-only). ONE seq space, APPEND-AT-EVENT: `append`
assigns the seq under a lock (the background-drive duplicate-seq race closes), and nothing
reserves a seq it might not use — the engine claims its phase seq only after the planner
returns (past the gate-suspension point), so a suspended gate burns nothing and the old
reserve-at-phase-start gaps disappear. Invocation entries are ANNOTATIONS of their phase:
they share its seq (P3's design, kept — an annotation is not a numbered step of its own, so
phase numbering and every golden seq are untouched). The replay contract is unchanged —
`rebuild()` consumes phase deltas only; every other kind is record, not state. The wire shape
is tolerant-additive on load (unknown fields ignored; an unknown FUTURE schema_version is
refused loudly rather than misread).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import Source
from ..domain.phase_result import PhaseResult

SCHEMA_VERSION = 2   # 2: typed entry kinds + append-at-event one-seq (v1 read-only supported)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JournalEntry(BaseModel):
    # tolerant-additive on the wire (part2 §1: replaces extra="forbid"): a v2+ journal with an
    # additive field still loads here; unknown KINDS still fail the Literal — additive means
    # fields, never semantics.
    model_config = ConfigDict(extra="ignore")

    seq: int = 0                             # 0 = unassigned — Journal.append stamps it
    ts: datetime
    # The typed entry kinds (part2 §1):
    #   phase         — the full PhaseResult delta (the replay payload)
    #   plan          — what the planner AUTHORED this phase BEFORE reduction: the tools it COULD
    #                   call (available), the intents it DECIDED to call (plan_calls), the direct
    #                   ops it authored (plan_ops), + its narrative. Shares its phase's seq (an
    #                   annotation, not a numbered step) and carries NO delta, so replay ignores
    #                   it. It makes the plan visible on BOTH paths — the call path AND the
    #                   scripted-direct-ops path, where there are no invocations to infer it from.
    #   gate_opened   — the proposed action + evidence shown to the human (record)
    #   gate_decision — approve/refine/deny + actor (the consent record)
    #   message       — an operator steer/answer (Source.HUMAN)
    #   invocation    — a capability call's boundary outcome (data | empty | error | blocked):
    #                   every call leaves a durable trace now, incl. an approved write's
    #                   execution — shares its phase's seq (annotation, not a numbered step)
    #   rejection     — a drop OUTSIDE a phase delta: the engine's op_ceiling truncation (M5),
    #                   which cut the planner's over-cap ops with no trace before. In-delta
    #                   reducer rejections still ride PhaseResult.rejections — not duplicated here.
    #   repair        — a planner repair record: an off-catalog tool / unparseable-or-illegal op /
    #                   coerced verdict the LIVE planner dropped BEFORE the reducer (M6). Used to
    #                   reach only the verbose log + dev summary; now durable and fed back.
    #   lifecycle     — run started/resumed/max-steps-exhausted/terminal outcome
    #   phase_review  — the between-phases DIRECTION review shown to the human (summary + the
    #                   proposed advance), durable like gate_opened (owner 2026-07-23)
    #   review_decision — approve/refine/deny of a phase-review + actor (the direction consent)
    #   step          — the v1 union of gate_decision+message, accepted read-only
    kind: Literal["phase", "plan", "step", "invocation", "gate_opened", "gate_decision",
                  "message", "rejection", "repair", "lifecycle",
                  "phase_review", "review_decision"] = "phase"
    phase_id: str | None = None              # playbook-declared phase id (P7 phase-as-data)
    actor: str = "engine"                    # WHO produced this entry (engine, or a human approver)
    source: Source | None = None             # provenance of a decision entry (Source.HUMAN on a gate answer)
    intent: str | None = None
    reasoning: str | None = None            # the narrative / thought
    delta: PhaseResult | None = None        # FULL delta (phase entries) — enables replay
    action: dict | None = None              # step: {capability, args}
    observation: dict | None = None         # step: {summary, evidence_ref}
    decision: str | None = None
    # PLAN annotation fields (kind="plan"): the planner's access surface + authored plan for
    # the audit — what it COULD call, what it DECIDED to call, and the direct ops it wrote.
    available: list[str] | None = None      # tools available that phase (PhaseSpec.allowed_intents)
    plan_calls: list[str] | None = None     # intended capability intents ([c.intent for c in plan.calls])
    plan_ops: list[str] | None = None       # direct-op summary ([type(o).__name__ for o in plan.ops])
    refs: dict = Field(default_factory=dict)  # derived index: {nodes, edges, facts, events, hypotheses}


class Journal:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.entries: list[JournalEntry] = []
        self._seq = 0
        self._lock = threading.Lock()        # appends are lock-guarded (one seq space, no dupes)
        self._clock = clock or _utcnow

    def reserve_seq(self) -> int:
        """Claim the next seq NOW (the engine claims its phase seq after the planner returns —
        past the suspension point — because materialized records stamp `created_by` before the
        phase entry can be appended). Claim-at-first-event, never reserve-then-maybe-append."""
        with self._lock:
            self._seq += 1
            return self._seq

    def append(self, entry: JournalEntry) -> JournalEntry:
        """Append-at-event: an entry arriving with seq=0 is assigned the next seq here, under
        the lock; an entry carrying a claimed seq (a phase, or its shared-seq invocation
        annotations) is accepted as-is and advances the watermark."""
        with self._lock:
            if entry.seq <= 0:
                self._seq += 1
                entry.seq = self._seq
            else:
                self._seq = max(self._seq, entry.seq)
            self.entries.append(entry)
        return entry

    def append_phase(self, seq: int, result: PhaseResult, actor: str = "engine") -> JournalEntry:
        refs = {
            "nodes": [n.id for n in result.nodes_touched],
            "edges": [e.id for e in result.edges_added],
            "facts": [f.id for f in result.facts_added],
            "events": [e.id for e in result.events_added],
            "hypotheses": [
                (d.hypothesis.id if d.hypothesis else d.hypothesis_id)
                for d in result.hypotheses_updated
            ],
        }
        return self.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="phase", phase_id=result.phase_id,
            actor=actor, reasoning=result.narrative, delta=result, refs=refs))

    def append_plan(self, seq: int, phase_id: str, *, tools_available: list[str],
                    calls: list[str], ops: list[str], narrative: str) -> JournalEntry:
        """Record the planner's PLAN for a phase (owner goal: 'the planner's PLAN + the TOOLS
        AVAILABLE'). Carries the access surface (`available` = allowed_intents), the intents it
        DECIDED to call (`plan_calls`), and the direct ops it authored (`plan_ops`) — so the plan
        is visible on the scripted-direct-ops path too, where zero invocations are emitted and the
        plan would otherwise be invisible except as after-the-fact facts. SHARES the phase's seq
        (an annotation, not a numbered step) and carries NO delta, so phase/step numbering — and
        every golden seq — is untouched and replay ignores it."""
        return self.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="plan", phase_id=phase_id, actor="engine",
            reasoning=narrative, available=list(tools_available),
            plan_calls=list(calls), plan_ops=list(ops)))

    def append_gate_opened(self, phase_id: str, *, gate_id: str, actions: list[dict],
                           reasoning: str, hypothesis: str | None,
                           evidence: list[str]) -> JournalEntry:
        """The gate OPENING is durable (part2 §1: it was an in-memory event only): what was
        proposed, on whose behalf (the serving hypothesis) and on what evidence — so the
        journal shows what the human was ASKED, not just what they answered."""
        return self.append(JournalEntry(
            ts=self._clock(), kind="gate_opened", phase_id=phase_id, actor="engine",
            intent=(actions[0].get("intent") if actions else None), reasoning=reasoning,
            action={"gate_id": gate_id, "actions": actions},
            observation={"hypothesis": hypothesis, "evidence": evidence}))

    def append_gate_decision(self, phase_id: str, *, intent: str, reasoning: str,
                             action: dict, observation: dict, decision: str,
                             actor: str) -> JournalEntry:
        return self.append(JournalEntry(
            ts=self._clock(), kind="gate_decision", phase_id=phase_id, actor=actor,
            source=Source.HUMAN, intent=intent, reasoning=reasoning, action=action,
            observation=observation, decision=decision))

    def append_phase_review(self, phase_id: str, *, review_id: str, to_phase: str,
                            summary: str, verdict: str, hypothesis: str | None,
                            facts: list[str], nodes: list[str]) -> JournalEntry:
        """The phase-review OPENING is durable (owner 2026-07-23), gate_opened-style: WHAT the
        phase accomplished (summary), the proposed advance (to_phase), the leading hypothesis and
        the delta ids it discovered — so the journal shows the DIRECTION the human was asked to
        approve, not just their answer. Seq is assigned at append (nothing reserved), so the
        completed phase behind it keeps its own seq gap-free."""
        return self.append(JournalEntry(
            ts=self._clock(), kind="phase_review", phase_id=phase_id, actor="engine",
            reasoning=summary,
            action={"review_id": review_id, "to_phase": to_phase, "verdict": verdict},
            observation={"hypothesis": hypothesis, "facts": facts, "nodes": nodes}))

    def append_review_decision(self, phase_id: str, *, review_id: str, to_phase: str,
                               decision: str, actor: str, reason: str = "") -> JournalEntry:
        """The human DIRECTION decision on a phase-review (approve/refine/deny) + WHO — the
        consent record for advancing (or repeating/halting), Source.HUMAN, seq assigned at append."""
        return self.append(JournalEntry(
            ts=self._clock(), kind="review_decision", phase_id=phase_id, actor=actor,
            source=Source.HUMAN, reasoning=reason or f"phase-review {decision} by {actor}",
            action={"review_id": review_id, "to_phase": to_phase},
            observation={"decision": decision, "actor": actor}, decision=decision))

    def append_message(self, phase_id: str | None, *, text: str, message_kind: str,
                       actor: str) -> JournalEntry:
        return self.append(JournalEntry(
            ts=self._clock(), kind="message", phase_id=phase_id, actor=actor,
            source=Source.HUMAN, intent="operator_message", reasoning=text,
            action={"kind": message_kind}, observation={"actor": actor}))

    def append_lifecycle(self, event: str, *, phase_id: str | None = None,
                         outcome: str | None = None, detail: dict | None = None) -> JournalEntry:
        """Run lifecycle record (part2 §1/§3: zombie states die diagnosable): started / resumed /
        max_steps_exhausted / closed — with the terminal outcome where one exists."""
        return self.append(JournalEntry(
            ts=self._clock(), kind="lifecycle", phase_id=phase_id, actor="engine",
            reasoning=event, decision=outcome,
            action={"event": event, **(detail or {})}))

    def append_rejection(self, seq: int, phase_id: str, *, op_kind: str, reason: str,
                         dropped: list[str] | None = None) -> JournalEntry:
        """A rejection OUTSIDE a phase delta (part2 §1): an op the ENGINE dropped — today the
        per-phase `op_ceiling` truncation (M5), which used to silently head-slice the planner's
        over-cap ops with no journal entry and no feedback while every OTHER drop in the system is
        first-class. SHARES its phase's seq (an annotation, not a numbered step) and carries NO
        delta, so phase/step numbering is untouched and replay ignores it. In-delta reducer
        rejections still ride PhaseResult.rejections — this kind is only for drops outside it."""
        return self.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="rejection", phase_id=phase_id, actor="engine",
            intent=op_kind, reasoning=reason,
            action={"op_kind": op_kind, "dropped": list(dropped or [])}))

    def append_repair(self, seq: int, phase_id: str, *, detail: str) -> JournalEntry:
        """A planner REPAIR record (part2 §1): the LIVE planner dropped an off-catalog tool
        intent, an unparseable/illegal op, or coerced a malformed verdict — BEFORE the reducer
        (which never saw it), so the drop used to reach only the verbose log + dev summary. Now it
        is durable AND fed back like a reducer rejection (M6 — the two enforcement channels unify).
        SHARES its phase's seq (an annotation, not a numbered step); carries NO delta (replay
        ignores it). Empty on the scripted path — a ScriptedPlanner emits no repairs."""
        return self.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="repair", phase_id=phase_id, actor="engine",
            reasoning=detail, action={"repair": detail}))

    def step_entries(self) -> list[JournalEntry]:
        """The human-in-the-loop entries (v2 gate_decision/message + the v1 "step" union)."""
        return [e for e in self.entries if e.kind in ("step", "gate_decision", "message")]

    def read(self) -> list[JournalEntry]:
        return list(self.entries)

    def phase_entries(self) -> list[JournalEntry]:
        return [e for e in self.entries if e.kind == "phase" and e.delta is not None]

    # ── NDJSON persistence (R-J4: versioned + partial-line-safe) ──────────────
    def to_ndjson(self) -> str:
        # The schema header carries its OWN kind ("header") so NO on-disk line is kind-less —
        # the owner's CLEAN rule ("every entry carries its kind; one coherent shape"). It stays
        # metadata (not a JournalEntry): from_ndjson strips it on load by its schema_version key,
        # exactly as before, so the additive `kind` is wire-safe and v1 headers still load.
        lines = [json.dumps({"schema_version": SCHEMA_VERSION, "kind": "header"})]
        lines += [e.model_dump_json() for e in self.entries]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_ndjson(cls, text: str, clock: Callable[[], datetime] | None = None) -> Journal:
        j = cls(clock=clock)
        raw = text.split("\n")
        # a partial final write leaves a truncated last line — drop anything unparseable at the tail
        parsed: list[dict] = []
        for i, line in enumerate(raw):
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                if i == len(raw) - 1 or all(not r.strip() for r in raw[i + 1:]):
                    break          # trailing partial line — safe to skip
                raise
        if parsed and "schema_version" in parsed[0]:
            # VALIDATED on load (part2 §1: was read-and-ignored). Tolerant-additive within a
            # known version (extra fields ignored above); an unknown FUTURE version refuses
            # loudly — misreading a future journal would corrupt the source of truth.
            version = parsed[0]["schema_version"]
            if not isinstance(version, int) or version > SCHEMA_VERSION:
                raise ValueError(f"journal schema_version {version!r} is newer than this "
                                 f"engine understands (max {SCHEMA_VERSION})")
            parsed = parsed[1:]
        for d in parsed:
            j.append(JournalEntry.model_validate(d))
        j._seq = max((e.seq for e in j.entries), default=0)
        return j
