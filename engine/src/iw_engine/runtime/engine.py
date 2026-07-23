"""Engine — the thin deterministic phase orchestrator (DESIGN §0/§2.3). One phase, one
loop iteration:  plan (typed ops) → reduce (validate+materialise) → apply to graph+store
→ gate the verdict → journal the PhaseResult → route to the next phase. No LangGraph, no
sub-agent sprawl — the uniform contract makes the whole controller a few lines. Fully
deterministic given a ScriptedPlanner + mock capabilities.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..capability.layer import CapabilityLayer, Invocation
from ..domain import registry
from ..domain.enums import Species, VerdictStatus
from ..domain.hypothesis import Hypothesis
from ..domain.phase_result import PhaseResult
from ..domain.playbook import Playbook
from ..domain.subject import SubjectRef
from ..graph.fold import apply_delta
from ..graph.graph import Graph
from ..graph.reducer import Rejection, materialize
from ..graph.tools import focus_slice
from ..hypothesis import belief
from ..hypothesis.store import HypothesisStore
from ..journal.journal import Journal, JournalEntry
from .controller import check_gate, next_phase
from .planner import PlanContext, Planner


def _produced_summary(ops: list) -> list[str]:
    """A per-op one-line summary of what a REASONED STEP produced (JOURNAL story fidelity) — the
    'produced facts' side of a tool call ("→ produced red_errors=0.40, red_rate=820 rpm, node
    error_signature npe-taxcalc"). ONE string per op (so the UI can disclose each produced datum);
    the fact/event species split mirrors the layer's `_summarize_ops`. Best-effort labelling +
    defensive: an op shape it does not model degrades to its class name, never raises."""
    out: list[str] = []
    for o in ops:
        name = type(o).__name__
        if name == "AddAssertion":
            kind = "event" if getattr(o, "species", None) == Species.EVENT else "fact"
            unit = f" {o.unit}" if getattr(o, "unit", None) else ""
            out.append(f"{kind} {o.name}={o.value}{unit}" if o.value is not None
                       else f"{kind} {o.name}")
        elif name == "AddNode":
            out.append(f"node {o.type.value} {_node_ident(o)}".rstrip())
        elif name == "AddEdge":
            out.append(f"link {o.type.value} {o.src}→{o.dst}")
        elif name == "ProposeHypothesis":
            out.append(f"hypothesis {o.hid}: {o.statement}")
        elif name == "UpdateHypothesis":
            out.append(f"hypothesis {o.hid} {o.new_status}".rstrip() if o.new_status
                       else f"hypothesis {o.hid}")
        elif name == "NoEvidence":
            out.append(f"no_evidence {o.intent} @ {o.scope}")
        else:
            out.append(name)
    return out


def _node_ident(o) -> str:
    """The identity value(s) that make an AddNode's entity THIS entity (NODE_SPECS.identity_keys),
    joined — falls back to all prop values. Best-effort labelling for the produced summary only,
    never load-bearing (the node id is the authority)."""
    from ..domain.nodes import NODE_SPECS
    spec = NODE_SPECS.get(o.type)
    keys = list(spec.identity_keys) if spec else list(o.props)
    return " ".join(str(o.props[k]) for k in keys if k in o.props)


@dataclass
class RunResult:
    subject: SubjectRef
    phases_run: list[str]            # playbook-declared phase ids (P7 phase-as-data)
    graph: Graph
    hypothesis_store: HypothesisStore
    journal: Journal
    confirmed: Hypothesis | None
    close_outcome: str | None        # playbook-declared outcome label (P7 step 4), None = open
    rejections: list[Rejection] = field(default_factory=list)
    invocations: list[Invocation] = field(default_factory=list)   # capability audit trail
    # the SUBJECT/ORIGIN node id per the playbook's subject_node role binding (P7 step 5:
    # "the incident is the first node" is playbook data — session/bundle read THIS, never
    # a hardcoded incident convention). None only on legacy disk reopens without the field.
    origin_node: str | None = None


class Engine:
    def __init__(self, playbook: Playbook, planner: Planner, *, clock=None,
                 layer: CapabilityLayer | None = None) -> None:
        self.playbook = playbook
        self.planner = planner
        self._clock = clock or (lambda: datetime.now(UTC))   # wall-clock for trace spans
        self.layer = layer   # owns the fetch transport (Source) since the §C re-seam
        self.graph = Graph()
        self.hypothesis_store = HypothesisStore()
        # P4 (DOMAIN-v3 §2.5): the store ranks/promotes on ENGINE-earned weighted evidence;
        # the anomaly ref is a closure so the bind survives the symptom being framed later.
        self.hypothesis_store.bind_scoring(self.graph, playbook.tunables,
                                           anomaly_ref=lambda: self._anomaly_ref)
        self.journal = Journal(clock=clock)
        self.subject: SubjectRef | None = None
        self._anomaly_ref: str | None = None
        self._gate_feedback: str | None = None   # last failed-gate reason, fed to the next plan (GAP 3)
        # last step's reducer rejections, fed to the NEXT plan (P3 step 2 — the bounded repair
        # loop: the model learns WHY an op was dropped instead of seeing silent nothing)
        self._last_rejections: list[Rejection] = []
        self.rejections: list[Rejection] = []
        self.invocations: list[Invocation] = []
        # F1 attribution — parallel to `invocations`: the plan to-do index each invocation served,
        # so the interactive session's live `capability_call` stream can group tool cards under
        # their to-do (the journal carries the same index on each invocation entry).
        self.invocation_todos: list[int | None] = []
        # P3 airlock step 1 — the engine CONSUMES the boundary outcome (part4-capability §4):
        # the LAST outcome per intent. An intent whose last call errored/was blocked carries no
        # evidentiary weight and may not feed the NoEvidence/refutation path; a later successful
        # call of the same intent clears the bar.
        self._intent_outcomes: dict[str, str] = {}
        # resumable run-state (A3) — the engine is a stepper; run() is a driver over step()
        self._phase: str | None = None
        self._phases_run: list[str] = []
        self._steps = 0
        self._max_steps = 60

    def start(self, subject: SubjectRef, *, max_steps: int = 60) -> None:
        """Begin an investigation; leaves the engine at the entry phase, nothing run yet."""
        self.subject = subject
        self._phase = self.playbook.entry_phase
        self._phases_run = []
        self._steps = 0
        self._max_steps = max_steps
        self._gate_feedback = None
        self._intent_outcomes = {}
        self._last_rejections = []

    def done(self) -> bool:
        return self._phase is None or self._steps >= self._max_steps

    @property
    def current_phase(self) -> str | None:
        """The phase the next `step()` will run (None once the run is complete). Lets a driver
        (the live runner, the interactive session backend) observe/scope per-phase state — e.g.
        a phase-scoped fixture transport — between steps without reaching into internals."""
        return self._phase

    def reenter_phase(self, phase_id: str) -> None:
        """Point the next `step()` back at `phase_id`. The interactive session's phase-review
        REFINE uses this to RE-RUN the just-completed phase with the operator's steer. Purely a
        between-steps pointer reset — NON-blocking and never called by `run()`/gen_golden/run_live,
        so the batch path and every golden are untouched (the review lives entirely in the driver)."""
        self._phase = phase_id

    def step(self) -> PhaseResult | None:
        """Run exactly one phase and route to the next. Returns the PhaseResult, or None when
        the investigation is complete. The interactive driver calls this to pause between
        phases (and, in interactive mode, before a gated write)."""
        if self.done():
            return None
        self._steps += 1
        spec = self.playbook.phase(self._phase)
        result = self._run_phase(self._phase, spec)
        self._phases_run.append(self._phase)
        nxt = next_phase(spec, result.verdict)
        if nxt is None and result.verdict.status is not VerdictStatus.DONE:
            # P7 step 4: an UNMAPPED verdict (a BLOCKED/BACKTRACK/ADVANCE this phase declares
            # no route for) is a journaled lifecycle event + an explicit terminal — never a
            # silent dead-end. (A gate-passed DONE is the normal terminal, no extra record.)
            self.journal.append_lifecycle(
                "unrouted_verdict", phase_id=self._phase,
                outcome=result.verdict.status.value,
                detail={"verdict": result.verdict.status.value,
                        "routes": dict(spec.on_verdict)})
        self._phase = nxt
        return result

    def run(self, subject: SubjectRef, *, max_steps: int = 60) -> RunResult:
        self.start(subject, max_steps=max_steps)
        while self.step() is not None:
            pass
        return self.result()

    def result(self) -> RunResult:
        confirmed = self.hypothesis_store.confirmed()
        outcome = self._close_outcome(self._phases_run, confirmed)
        origin = (registry.subject_node_id(self.playbook.subject_node, self.subject.id)
                  if self.subject is not None else None)
        return RunResult(subject=self.subject, phases_run=self._phases_run, graph=self.graph,
                         hypothesis_store=self.hypothesis_store, journal=self.journal,
                         confirmed=confirmed, close_outcome=outcome, rejections=self.rejections,
                         invocations=self.invocations, origin_node=origin)

    # ── one phase ─────────────────────────────────────────────────────────────
    def _run_phase(self, phase: str, spec) -> PhaseResult:
        # P4: the abstract `correlate_timeline` intent resolves to ENGINE code — every
        # phase whose playbook declares it (hypothesize/investigate in the core playbook)
        # receives the deterministic skew-tolerant change→onset candidates as a plan hint.
        correlations = (belief.correlate_timeline(self.graph, self.playbook.tunables,
                                                  anomaly_ref=self._anomaly_ref)
                        if "correlate_timeline" in spec.allowed_intents else [])
        # P7 (projections drive reasoning): every plan receives the B9.3 focus slice — the
        # bounded, tiered reasoning view (cause path + suspects + frontier in full, healthy
        # collapsed to counts) — in place of the old flat full-graph-capped render_slice dump,
        # paired with the ranked hypothesis summary (root + evidence counts, so a planner can
        # target refutation) and the P4 correlations.
        t = self.playbook.tunables
        ctx = PlanContext(
            subject=self.subject, phase=phase, phase_spec=spec, goal=spec.goal,
            focus=focus_slice(self.graph, self._anomaly_ref, t.focus_budget,
                              max_facts_per_node=t.focus_facts_per_node,
                              frontier_hops=t.focus_frontier_hops),
            hypotheses=[{"id": h.id, "statement": h.statement, "status": h.status.value,
                         "confidence": self.hypothesis_store.score(h),
                         "root_candidate": h.root_candidate,
                         "supporting": len(h.supporting_facts),
                         "refuting": len(h.refuting_facts)}
                        for h in self.hypothesis_store.ranked()],
            entry_phase=self.playbook.entry_phase,
            tunables=t, gate_feedback=self._gate_feedback,
            rejections=list(self._last_rejections), correlations=correlations)
        plan = self.planner.plan(ctx)
        # JOURNAL v2 (part2 §1): the phase seq is CLAIMED here — after the planner returned,
        # past the only suspension point (an interactive write-gate suspends by raising from
        # plan()) — never reserved at phase start. A suspended gate therefore burns nothing:
        # the old reserve-then-append TOCTOU's unlabeled seq gaps are gone. The claim must
        # precede materialization because every record stamps `created_by` with this seq.
        seq = self.journal.reserve_seq()

        # JOURNAL the PLAN (owner goal: goal + PLAN + TOOLS AVAILABLE, from the start). An
        # annotation sharing the phase seq (replay-inert): the tools the planner COULD call
        # (spec.allowed_intents), the intents it DECIDED to call, and the direct ops it authored.
        # Emitted for BOTH the call path AND the scripted-direct-ops path — so a plan that authors
        # its evidence as direct ops (zero invocations) stops being invisible provenance.
        self.journal.append_plan(
            seq, phase, tools_available=list(spec.allowed_intents),
            calls=[c.intent for c in plan.calls],
            ops=[type(o).__name__ for o in plan.ops], narrative=plan.narrative,
            todos=self._todo_views(plan))   # F1: the plan as a checklist of to-dos on the record

        # M6: JOURNAL the planner's OWN reject+repair drops (off-catalog tool, unparseable/illegal
        # op, coerced verdict) — `repair` annotations sharing the phase seq (replay-inert). They
        # are ALSO fed to the next plan below, unifying the planner's enforcement channel with the
        # reducer's: a planner-dropped op is now as durable + fed-back as a reducer-dropped one
        # (was verbose-log / dev-summary only). Empty on the scripted path — goldens untouched.
        for _repair in plan.repairs:
            self.journal.append_repair(seq, phase, detail=_repair)

        # capability calls -> data ops (the tool outputs fold into the graph); writes
        # (remediation actions) execute only in a human-gated `writes_allowed` phase. serve()
        # is gate-first: a disallowed write is blocked BEFORE any fetch/side-effect (§C.3/§D).
        data_ops: list = []
        if self.layer is not None:
            allow_write = spec.writes_allowed          # domain role-binding, not a hardcoded phase
            # F1: the to-do index each flat call serves (parallel to plan.calls) — stamped on the
            # invocation record + the live stream so each tool call shows which to-do it executed for.
            call_todos = plan.call_todo_indices()
            effective = plan.effective_todos   # the plan AS a checklist (authored or synthesized)
            for i, call in enumerate(plan.calls):
                todo_idx = call_todos[i] if i < len(call_todos) else None
                started = self._clock().isoformat()
                t0 = time.perf_counter()
                ops_i, inv = self.layer.serve(call, allow_write=allow_write)
                dur_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                # stamp the trace span (obs 9); a write served under a gate is a "workflow" step,
                # a plain read is a "tool" call. Timing is wall-clock, ephemeral (not journaled).
                inv = inv.model_copy(update={
                    "started_at": started, "duration_ms": dur_ms,
                    "kind": "workflow" if inv.effect.value == "write" else "tool"})
                data_ops.extend(ops_i)
                self.invocations.append(inv)
                self.invocation_todos.append(todo_idx)   # F1 attribution, parallel to invocations
                # P3 airlock step 1 + JOURNAL v2 unification (part2 §1): consume the boundary
                # outcome AND journal EVERY call — data-bearing, clean-empty, error and blocked
                # alike — so an approved write can never again leave zero durable trace ("the
                # journal proves consent, never execution"). Outcomes stay DISTINCT downstream
                # (error ≠ clean-empty is the honesty line).
                self._intent_outcomes[inv.intent] = inv.outcome
                # the WHY each tool was called must be PER-CALL (JOURNAL story fidelity): the call's
                # own `rationale`, else the serving to-do's objective, else the phase narrative —
                # never one generic why for every call. The serving to-do also attributes the human
                # RESULT line + a produced-ops summary + the op-count to the invocation, so the
                # journal reads as a reasoned step ("called X → THIS came back → produced these
                # facts"). `todo` is the F1 attribution — which plan to-do this call served.
                serving = (effective[todo_idx]
                           if todo_idx is not None and todo_idx < len(effective) else None)
                why = call.rationale or (serving.objective if serving else "") or plan.narrative
                # the RESULT/PRODUCED/op-count attribution rides ONLY when the plan AUTHORED to-dos:
                # a synthesized default to-do wraps ALL of the phase's ops, so attributing them to
                # one call would be wrong — leaving these None keeps the no-authored-to-dos path
                # (every existing scenario) byte-for-byte unchanged (goldens are byte-identical).
                observation_text = produced = op_count = None
                if plan.todos and serving is not None:
                    observation_text = serving.observation or None
                    produced = _produced_summary(serving.ops)
                    op_count = len(serving.ops) if len(serving.ops) > inv.op_count else None
                self._journal_invocation(seq, phase, inv, why=why, todo=todo_idx,
                                         observation_text=observation_text, produced=produced,
                                         op_count=op_count)
        # The per-phase op ceiling guards against RUNAWAY PLANNER OUTPUT only — it never
        # touches the adapters' data ops. Those are deterministic tool folds and internally
        # referential (a call's AddAssertion lands on a node a sibling call's AddNode creates);
        # the old `(data_ops + plan.ops)[:ceiling]` cut proved doubly unsafe on the live
        # path (retest 2026-07-22): first it silently truncated the model's propose/update/
        # cleared-event ops (plan.ops sat LAST behind 45-52 data ops vs investigate's
        # ceiling of 40 — the store stayed empty after a propose turn, gates never opened,
        # the repeat cap force-advanced without a refuted rival); then, with plan ops
        # protected, it cut a later call's AddNode out from under the facts referencing it
        # (the firewall run's 16 'unknown subject external_service:...' rejections —
        # flowmap's discovery node was the op the ceiling dropped). Vendor-volume bounding
        # belongs in the adapters, not a fold-time cut that breaks batch integrity.
        ceiling = self.playbook.tunables.op_ceiling.get(phase)
        authored = list(plan.ops)
        plan_ops = authored[:ceiling] if ceiling else authored
        # M5: the op_ceiling used to SILENTLY head-slice the planner's over-cap ops — no journal,
        # no feedback — while every OTHER drop in the system is first-class. When it cuts, record a
        # `rejection` entry (shares the phase seq, replay-inert) naming WHAT was dropped + WHY, and
        # hand the drop to the NEXT plan (below) so the planner learns its output was capped. F1
        # landed the per-to-do budget SEAM (`Todo.op_budget`) as the documented home for a future
        # dependency-aware reserve-quota (dropping whole to-do items, not a tail-slice); it is
        # deliberately UNWIRED here, so the per-phase op_ceiling behaviour is byte-for-byte unchanged.
        ceiling_rejection: Rejection | None = None
        if ceiling and len(authored) > ceiling:
            cut = authored[ceiling:]
            dropped = [type(o).__name__ for o in cut]
            reason = (f"op_ceiling[{phase}]={ceiling}: planner authored {len(authored)} ops, "
                      f"dropped {len(cut)} over the cap ({', '.join(dropped)})")
            self.journal.append_rejection(seq, phase, op_kind="op_ceiling", reason=reason,
                                          dropped=dropped)
            ceiling_rejection = Rejection(op_index=ceiling, op_kind="op_ceiling", reason=reason)
        ops = data_ops + plan_ops

        # intents whose LAST call errored/was blocked observed NOTHING — a NoEvidence op naming
        # one is rejected by the reducer (fabricated-negative-evidence killer, part4 §4).
        no_weight = frozenset(i for i, o in self._intent_outcomes.items()
                              if o in ("error", "blocked"))
        mat = materialize(ops, seq, self.graph, self.playbook.tunables,
                          anomaly_ref=self._anomaly_ref, no_weight_intents=no_weight)
        self.rejections.extend(mat.rejections)
        self._last_rejections = list(mat.rejections)   # the NEXT plan is told what was dropped
        # M5/M6: unify the drop channels into that feedback — the reducer's in-delta rejections
        # PLUS the planner's own repairs (op_index=-1: pre-reducer, no op index) and the op_ceiling
        # cut. The planner now sees EVERY drop, not just the reducer's. (These extras live only in
        # the journal + this feedback — never in PhaseResult.rejections / bundle["rejections"],
        # which stay the reducer-in-delta projection.)
        self._last_rejections += [Rejection(op_index=-1, op_kind="repair", reason=r)
                                  for r in plan.repairs]
        if ceiling_rejection is not None:
            self._last_rejections.append(ceiling_rejection)

        # capture the symptom node the first time it is created (domain role-binding)
        if self._anomaly_ref is None:
            for n in mat.nodes:
                if n.type == self.playbook.symptom_node:
                    self._anomaly_ref = n.id
                    break

        result = PhaseResult(
            phase_id=phase, goal_restated=spec.goal, facts_added=mat.facts,
            events_added=mat.events, spans_added=mat.spans,
            nodes_touched=mat.nodes, edges_added=mat.edges,
            hypotheses_updated=mat.hyp_deltas, narrative=plan.narrative,
            next_actions=plan.next_actions, verdict=plan.verdict,
            retractions=mat.retractions,   # tombstones ride the delta (P3 step 6 — R-J3)
            remaps=mat.remaps,             # identity graduations ride the delta (P5 — §9.2)
            rejections=mat.rejections)   # journaled with the delta (P3 step 2 — never memory-only)

        # apply the delta via the single mutation seam FIRST, then gate against the updated store
        apply_delta(result, seq, self.graph, self.hypothesis_store)

        gated = check_gate(spec, result, self.hypothesis_store, self.playbook.tunables,
                           graph=self.graph, journal=self.journal,
                           anomaly_ref=self._anomaly_ref,
                           symptom_cleared_event=self.playbook.symptom_cleared_event)
        # REPEAT CAP (tunables.max_retries): a live planner can vote REPEAT indefinitely (the
        # database run looped in TRIAGE for all 16 steps). After max_retries prior consecutive
        # runs of this phase, force an ADVANCE so the investigation always progresses — the
        # engine's guardrail, independent of the model's judgment. Deterministic scripts never
        # hit it (they emit ADVANCE when ready), so goldens are untouched.
        prior_consecutive = 0
        for p in reversed(self._phases_run):
            if p == phase:
                prior_consecutive += 1
            else:
                break
        if (gated.status == VerdictStatus.REPEAT
                and prior_consecutive >= self.playbook.tunables.max_retries
                and spec.on_verdict.get("advance") is not None):
            reason = (gated.gate_reason or "") + " [repeat cap reached — forced advance]"
            gated = gated.model_copy(update={"status": VerdictStatus.ADVANCE, "gate_reason": reason})
        result = result.model_copy(update={"verdict": gated})
        # remember WHY a gate failed so the NEXT plan is told (clears on a pass) — GAP 3
        self._gate_feedback = gated.gate_reason
        self.journal.append_phase(seq, result)
        return result

    @staticmethod
    def _todo_views(plan) -> list[dict]:
        """The plan's to-dos as journal dicts (F1): each objective + its call intents + op kinds +
        status, so the `plan` entry carries the CHECKLIST. The op_budget/delegate SEAMS ride only
        when set, so a golden's to-do stays the lean {objective, calls, ops, status}. Reads the
        DERIVED `effective_todos`, so a scripted plan that authored none still records its single
        synthesized to-do (the checklist is never empty when the phase did anything)."""
        out: list[dict] = []
        for td in plan.effective_todos:
            d: dict = {"objective": td.objective,
                       "calls": [c.intent for c in td.calls],
                       "ops": [type(o).__name__ for o in td.ops],
                       "status": td.status.value}
            # the human RESULT line for the step (JOURNAL story fidelity) — omitted when unset, so a
            # golden's synthesized default to-do (observation="") keeps its lean pre-story shape.
            if td.observation:
                d["observation"] = td.observation
            if td.op_budget is not None:
                d["op_budget"] = td.op_budget
            if td.delegate:
                d["delegate"] = True
            out.append(d)
        return out

    def _journal_invocation(self, seq: int, phase: str, inv, why: str | None = None,
                            todo: int | None = None, *, observation_text: str | None = None,
                            produced: list[str] | None = None,
                            op_count: int | None = None) -> None:
        """Journal ONE capability call's boundary outcome (P3 airlock step 1, extended by
        JOURNAL v2 to EVERY call — part2 §1's invocation row: an approved write used to leave
        zero durable trace; now intent, provider, params, effect, blocked-ness, op-count AND the
        WHY are on the record). The entry keys the outcome on `decision`/`observation.outcome`,
        keeping error ≠ clean-empty DISTINGUISHABLE downstream (part4-capability §4). `why` is the
        PER-CALL rationale (call.rationale → serving to-do objective → phase narrative), replacing
        the old single generic phase-level why. These are `kind="invocation"` entries: they SHARE
        the phase's seq (an annotation of that phase, not a numbered step of their own), so
        phase/step numbering — and every golden seq — is untouched; replay ignores them (no delta).
        Wall-clock timing stays ephemeral on the in-memory Invocation (trace concern); params ride
        in full — hashing them is a live-privacy knob for a later phase.

        The REASONED-STEP attributes (JOURNAL story fidelity) ride ADDITIVELY when the serving plan
        authored a to-do: `observation_text` (the human 'what came back' line) lands on
        `action.result`, `produced` (a per-op summary of what the step produced) on
        `action.produced`, and `op_count` OVERRIDES the fold's count with the serving to-do's op
        count (the mock fold is empty when a reasoned step's ops are planner-direct). All three are
        omitted-when-None, so an invocation from a plan that authored no to-do keeps its exact
        pre-story shape and every existing golden stays byte-identical."""
        action = {"capability": inv.intent, "provider": inv.provider,
                  "params": dict(inv.params), "effect": inv.effect.value,
                  # transport provenance (M1): HOW the raw was fetched — the served-by transport
                  # + the adapter's declared Binding, so the audit shows mock-vs-live on the record.
                  "served_by": inv.served_by,
                  "binding": inv.binding.value if inv.binding else None}
        # the reasoned-step RESULT + PRODUCED facts, attributed from the serving to-do (omitted
        # when absent → today's invocation shape, so the existing scenarios' goldens are unchanged).
        if observation_text is not None:
            action["result"] = observation_text
        if produced is not None:
            action["produced"] = list(produced)
        self.journal.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="invocation",
            phase_id=phase, actor="engine", intent=inv.intent, reasoning=why, todo=todo,
            action=action,
            observation={"outcome": inv.outcome, "reason": inv.reason, "blocked": inv.blocked,
                         "op_count": op_count if op_count is not None else inv.op_count},
            decision=inv.outcome))

    def _close_outcome(self, phases_run: list[str], confirmed) -> str | None:
        """The terminal outcome LABEL — playbook data, not an engine enum (P7 step 4): the
        playbook's OutcomeRule maps confirmed-root / no-confirmed-root to its own labels."""
        if self.playbook.terminal_phase not in phases_run:
            return None
        o = self.playbook.outcomes
        return o.confirmed_root if confirmed is not None else o.no_confirmed_root
