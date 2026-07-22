"""live_planner.py — a REAL-LLM Planner behind the same `Planner` Protocol the deterministic
`ScriptedPlanner` implements (principle 11: JUDGMENT is a swappable seam). `plan(ctx)` builds
a prompt from {the playbook's DOCTRINE (persona, evidence contracts, rooting conventions —
data, never engine code: Part III §3), phase goal + gate, the catalog grammar, the concrete
tool list, the current graph slice, the ranked hypotheses}, asks the LLM for ONE JSON plan,
and maps that plan to a `PlanOutput` (typed ops only — prose is a field, not the channel).
Off-catalog output is rejected+repaired here (invalid enum member, unparseable op, unknown
tool) BEFORE it reaches the reducer, which is the second, authoritative guard.

The LLM never emits free-form graph mutations except through the closed op set:
  calls:[{intent,params}]        -> CapabilityCall  (tool -> data ops via the layer)
  ops:[typed op dicts]           -> AddNode/AddFact/AddEvent/AddEdge/Propose/Update/NoEvidence
  narrative, verdict{status,confidence_level,basis}, next_actions
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from ..capability.layer import CapabilityCall
from ..domain import dictionary
from ..domain.common import Confidence
from ..domain.enums import (
    ConfidenceLevel,
    EdgeType,
    FactState,
    HypothesisStatus,
    NodeType,
    OpKind,
    Origin,
    Source,
    VerdictStatus,
)
from ..domain.operations import (
    AddEdge,
    AddEvent,
    AddFact,
    AddNode,
    Merge,
    NoEvidence,
    Operation,
    ProposeHypothesis,
    Retract,
    Retype,
    UpdateHypothesis,
)
from ..domain.phase_result import PhaseVerdict
from ..domain.playbook import Doctrine
from ..graph import tools as graph_tools
from .planner import PlanContext, PlanOutput


def _hid(x) -> str:
    """Normalize a hypothesis local id. The graph shows a hypothesis NODE as `hyp:h1`; if the
    model round-trips that displayed id back as the hid, the builder would re-prefix it to
    `hyp:hyp:h1` and fragment the hypothesis store. Strip a leading `hyp:` so `h1` and `hyp:h1` both map
    to the same hypothesis store entry (reject+repair)."""
    s = str(x).strip()
    return s[4:] if s.startswith("hyp:") else s


_ID_KEYS = ("sha", "change_id", "segment_id", "db_id", "signature_hash", "alert_id",
            "incident_id", "anomaly_id", "service_name")


def _props_txt(props: dict) -> str:
    """The discriminating props (change_type/file_line/...) the id alone hides — identity
    keys (already in the id) and hypothesis statements (rendered elsewhere) are dropped."""
    return ", ".join(f"{k}={v}" for k, v in props.items()
                     if v is not None and k not in _ID_KEYS and k != "statement")


def render_focus(slice_: dict) -> str:
    """Render `graph/tools.focus_slice` as the live prompt's graph view (P7: the B9.3 tiered
    reasoning view REPLACES the old flat full-graph dump `render_graph_full`). Full-tier nodes
    get complete evidence cards (latest fact per predicate); the frontier is the structural
    expansion surface; everything healthy/unimplicated is a count, never noise; refuted causal
    claims render as RULED OUT so the planner does not re-suggest them."""
    if not slice_ or not slice_.get("total"):
        return "(graph is empty — nothing discovered yet)"
    head = (f"FOCUS: {slice_.get('focus') or '(symptom node not framed yet)'}   "
            f"total={slice_['total']} nodes = {len(slice_['nodes'])} full "
            f"+ {len(slice_['frontier'])} frontier + {slice_['collapsed_count']} collapsed"
            + (f" {slice_['collapsed_types']}" if slice_.get("collapsed_types") else ""))
    lines = [head, "NODES (tiered by investigative relevance — full evidence cards):"]
    for n in slice_["nodes"]:
        line = f"  [{n['tier']}] {n['id']} ({n['type']})"
        ptxt = _props_txt(n.get("props") or {})
        if ptxt:
            line += f"  props: {ptxt}"
        ftxt = ", ".join(f"{f['predicate']}={f['value']}{f['unit'] or ''}"
                         for f in n.get("facts") or [])
        if ftxt:
            line += f"  facts: {ftxt}"
        lines.append(line)
    if slice_.get("frontier"):
        lines.append("FRONTIER (structural expansion surface — candidate evidence targets):")
        lines += [f"  {n['id']} ({n['type']})  {n['hops']} hop(s) from "
                  + (", ".join(n["attached_to"]) or "the full tier")
                  for n in slice_["frontier"]]
    if slice_.get("edges"):
        lines.append("EDGES:")
        lines += [f"  {e['type']}: {e['src']} -> {e['dst']} [{e['origin']}]"
                  for e in slice_["edges"]]
    if slice_.get("ruled_out"):
        lines.append("RULED OUT (retracted causal claims — do NOT re-propose these):")
        lines += [f"  {e['src']} -x-> {e['dst']} ({e['type']})" for e in slice_["ruled_out"]]
    return "\n".join(lines)


# ── LLM clients — implementation lives in llm_client.py (the pluggable seam) ───
# Re-exported here for back-compat: `from iw_engine.runtime.live_planner import XaiClient`
# keeps working. New code should import from `llm_client` directly. To plug in ANY LLM,
# implement the `LLMClient` Protocol (a `complete_json(system,user)->dict` + a `.name`)
# and pass it to LivePlanner / live_build_manager, or register it in `make_llm_client`.
from .llm_client import (  # noqa: E402, F401
    GeminiClient,
    LLMClient,
    XaiClient,
    loads_salvage,
    make_llm_client,
    retry_delay,
)

# legacy underscore aliases (old imports `from live_planner import _loads_salvage`)
_loads_salvage = loads_salvage
_retry_delay = retry_delay



# ── the system prompt — ENGINE constitution assembled around PLAYBOOK doctrine ──
# The engine owns only the MECHANICS of the closed grammar: vocabulary discipline, the edge
# ban, graph-lag, id/hid conventions, the abstract-vs-concrete intent rule, and the JSON
# envelope. Every piece of DOMAIN method — persona, evidence contracts, fault-class rooting,
# progression — is `Playbook.doctrine` (Part III §3: prompt doctrine as playbook data), and
# every validity list is DERIVED from its enum, so the prompt can never again drift from the
# grammar the way the hand-restated source list dropped `bigpanda`.

_RULE_CLOSED_VOCAB = """\
- Use ONLY node/edge types and intents from the provided grammar/tool list. Inventing a
  label, an illegal edge pair, or an unknown tool wastes the turn (it is rejected)."""

_RULE_EDGE_BAN = """\
- NEVER emit `add_edge`. Every structural edge (depends_on, connects_to, ...) comes from a tool
  and every causal/evidence edge (caused_by, supports, refutes) is DERIVED by the engine from
  your hypothesis's root_candidate + supporting/refuting basis. A hand-authored edge is rejected
  and wastes the turn — encode causation in the hypothesis, not an edge."""

_RULE_GRAPH_LAG = """\
- A tool's data lands in the graph you see on your NEXT turn — so read the CURRENT GRAPH
  slice carefully; it holds the evidence from everything you called before."""

_RULE_ID_COPY = """\
- root_candidate of a hypothesis MUST be a node id copied from the graph slice (the
  initiating change/commit/segment/db), never prose."""

_RULE_HID = """\
- A hypothesis `hid` is a SHORT local id like "h1"/"h2" (NOT "hyp:h1"). The graph shows a
  hypothesis node as "hyp:h1" — to update it, pass hid "h1". Refute a rival by updating its
  hid with new_status "refuted"; do NOT re-propose it under a new id."""

_RULE_ABSTRACT_INTENTS = """\
- The phase's `allowed_intents` are ABSTRACT CATEGORIES, not tool names. Always call CONCRETE
  tools from the AVAILABLE TOOLS list. If a set of calls added NO new nodes/facts to the graph
  last turn, that tool was not wired for this incident — switch to a DIFFERENT wired tool; never
  repeat a call that returned nothing."""

_OUTPUT_CONTRACT = """\
OUTPUT: a single JSON object, no markdown, exactly:
{
  "reasoning": "your differential reasoning for THIS phase (2-5 sentences)",
  "calls": [{"intent": "<tool intent>", "params": {}}],
  "ops": [
    {"op":"add_node","type":"anomaly","props":{"anomaly_id":"ANOM-1"}},
    {"op":"add_fact","subject":"anomaly:anom-1","predicate":"onset_value","value":5200,"unit":"ms","source":"prometheus","at":"2026-07-19T14:05:00+00:00"},
    {"op":"add_event","entity":"anomaly:anom-1","type":"cleared","source":"prometheus","at":"2026-07-19T14:50:00+00:00"},
    {"op":"propose_hypothesis","hid":"h1","statement":"...","root_candidate":"<node id>","confidence_level":"med"},
    {"op":"update_hypothesis","hid":"h2","new_status":"refuted","basis":"...","add_refuting":[]},
    {"op":"no_evidence","intent":"healthrule_violations","scope":"<node id>","basis":"...","at":"2026-07-19T14:20:00+00:00"}
  ],
  "narrative": "concise phase narrative for the journal",
  "verdict": {"status":"advance|repeat|backtrack|blocked|done","confidence_level":"low|med|high","basis":"why this verdict"},
  "next_actions": ["what the next phase should do"]
}"""

_RETRACT_NOTE = """\
(`retract` tombstones a WRONG fact/event you previously folded — {"op":"retract","target":"<fact/event/edge id>","reason":"..."}; use it only for observations proven wrong, never to hide refuting evidence.)"""

# The op kinds the model may author = exactly `_parse_op`'s dispatch set, derived from the
# enum. ADD_ASSERTION is excluded on purpose: it is the adapters' NATIVE atom (P1b); the
# model authors facts/events through the add_fact/add_event compat shims — advertising the
# atom would invite ops the parser drops.
_PLANNER_OP_KINDS: tuple[str, ...] = tuple(
    k.value for k in OpKind if k is not OpKind.ADD_ASSERTION)


def render_system(doctrine: Doctrine) -> str:
    """Assemble the live planner's system prompt: the engine constitution (grammar discipline,
    turn/id mechanics, the JSON envelope) interleaved — in the exact battle-tested order the
    former `_SYSTEM` constant used — with the playbook's doctrine fragments, closed by
    validity lists DERIVED from OpKind/Source/HypothesisStatus. Content-equivalent to the old
    constant, with ONE deliberate fix: the derived source list carries `bigpanda` (the proven
    drift the hand-restated list had)."""
    return "\n".join([
        doctrine.persona,
        "",
        "HARD RULES",
        _RULE_CLOSED_VOCAB,
        doctrine.evidence_ops,
        _RULE_EDGE_BAN,
        doctrine.fact_rules,
        doctrine.frame_contract,
        _RULE_GRAPH_LAG,
        _RULE_ID_COPY,
        _RULE_HID,
        doctrine.rooting,
        doctrine.investigate_advance,
        doctrine.verify_advance,
        _RULE_ABSTRACT_INTENTS,
        doctrine.hypothesis_method,
        "",
        _OUTPUT_CONTRACT,
        "valid op kinds: " + ", ".join(_PLANNER_OP_KINDS) + ".",
        _RETRACT_NOTE,
        "valid sources: " + ", ".join(s.value for s in Source) + ".",
        "valid hypothesis statuses: " + ", ".join(s.value for s in HypothesisStatus) + ".",
    ])


@lru_cache(maxsize=1)
def _default_doctrine() -> Doctrine:
    """The packaged incident playbook's doctrine — the fallback for a LivePlanner constructed
    without an explicit one (every current wiring site), so doctrine-as-data lands with zero
    call-site churn. A playbook that drives the live planner with a DIFFERENT method passes
    its own `doctrine=` instead."""
    from .loader import load_playbook  # runtime sibling; imported lazily to stay cycle-proof
    pb = load_playbook(Path(__file__).resolve().parents[1] / "playbooks" / "incident.yaml")
    if pb.doctrine is None:  # the packaged playbook always carries one — fail loudly, not silently
        raise ValueError("packaged incident playbook carries no doctrine block")
    return pb.doctrine


@dataclass
class PhaseTrace:
    phase: str
    reasoning: str
    narrative: str
    verdict: str
    calls: list[str] = field(default_factory=list)
    proposed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class LivePlanner:
    """A live-LLM planner. Interchangeable with ScriptedPlanner behind the Planner Protocol."""

    def __init__(self, client, catalog_text: str, tools_text: str, tool_intents: set[str],
                 *, available_sources: set[str] | None = None,
                 doctrine: Doctrine | None = None,
                 default_at: datetime | None = None, verbose: bool = True):
        self.client = client
        self.catalog_text = catalog_text
        self.tools_text = tools_text
        self.tool_intents = set(tool_intents)
        # the playbook-authored method (persona/contracts/rooting/progression). The system
        # prompt is ASSEMBLED from it + the engine constitution — no doctrine in engine code.
        self.doctrine = doctrine or _default_doctrine()
        self.system = render_system(self.doctrine)
        # the tool intents actually wired with data this incident (like "connected integrations").
        self.available_sources = set(available_sources) if available_sources is not None else None
        self.default_at = default_at or datetime(2026, 7, 19, 14, tzinfo=UTC)
        self.verbose = verbose
        self.graph = None   # optional live Graph ref -> full view (set by the runner)
        self.traces: list[PhaseTrace] = []
        self.repairs: list[str] = []   # every repair (drop/coerce) across the run
        self._attempts: dict[str, int] = {}   # phase -> replan count (gate-failure feedback)
        self._called: set[str] = set()         # intents already invoked this run

    # ── Planner.plan ──────────────────────────────────────────────────────────
    def plan(self, ctx: PlanContext) -> PlanOutput:
        self._attempts[ctx.phase] = self._attempts.get(ctx.phase, 0) + 1
        user = self._build_prompt(ctx)
        raw = self.client.complete_json(self.system, user)
        out = self._to_plan_output(ctx, raw)
        self._called.update(c.intent for c in out.calls)
        return out

    # ── prompt ────────────────────────────────────────────────────────────────
    def _build_prompt(self, ctx: PlanContext) -> str:
        spec = ctx.phase_spec
        gate_bits = []
        if spec.gate.min_facts:
            gate_bits.append(f"min_facts>={spec.gate.min_facts}")
        if spec.gate.promotion:
            gate_bits.append(f"leading confidence>={ctx.tunables.confidence_gate}")
        if spec.gate.refutation_attempted:
            gate_bits.append("a rival refuted OR the leader challenged")
        if spec.gate.symptom_cleared:
            gate_bits.append("the symptom node carries its cleared event (recovery proven)")
        if spec.gate.human_approved:
            gate_bits.append("a human approval recorded for this phase")
        gate = "; ".join(gate_bits) or "(none — just produce the required fields)"

        wired = ""
        if self.available_sources is not None:
            wired = ("\n# TOOLS WIRED FOR THIS INCIDENT (ONLY these return data; any other\n"
                     "# tool resolves but returns EMPTY — do not waste calls on them):\n#   "
                     + ", ".join(sorted(self.available_sources)))

        write_hint = ""
        if spec.writes_allowed:
            # role binding (P7 phase-as-data): the hint keys on `writes_allowed`, never a phase name
            write_hint = (
                "\n# THIS PHASE MAY EXECUTE WRITES: propose the concrete fix as an `apply_remediation` "
                "capability CALL (a WRITE) — e.g. calls:[{intent:'apply_remediation', params:{action:'<the "
                "reversible fix>', reversible:true}}]. This WRITE opens the human approval gate; do NOT just "
                "describe the fix in prose or you will skip the human-in-the-loop approval.")

        correlated = ""
        if ctx.correlations:
            # P4: the engine-computed skew-tolerant change→onset correlation — the
            # executable home of the abstract `correlate_timeline` intent. Ordering is
            # only ever asserted OUTSIDE the combined clock-skew bound (R-J2).
            lines = []
            for c in ctx.correlations[:8]:
                order = ("preceded onset" if c.get("ordering_certain")
                         else "within the clock-skew bound of onset — do NOT assert it came first")
                lines.append(f"#   - {c.get('type')} on {c.get('entity')} at "
                             f"{c.get('occurred_at')} (lead {c.get('lead_s')}s; {order})")
            correlated = ("\n# TEMPORALLY-CORRELATED CHANGE EVENTS (engine-computed, "
                          "clock-skew-tolerant; candidates for the change-first hypothesis):\n"
                          + "\n".join(lines))

        steer = ""
        if ctx.messages:
            # operator steering from the two-way chat (obs 2) — the human in the loop. Recent last.
            lines = "\n".join(f"#   - {m.get('text', '')}" for m in ctx.messages[-6:])
            steer = ("\n# OPERATOR STEERING (the human investigating with you said this — weigh it "
                     "heavily, it may redirect your hypothesis or tool choice):\n" + lines)

        dropped = ""
        if ctx.rejections:
            # the bounded repair loop (P3 step 2): each dropped op's exact reason, so the model
            # REPAIRS (fix the subject/name/edge pair) instead of re-emitting into silence.
            lines = "\n".join(f"#   - [{r.op_kind}] {r.reason}" for r in ctx.rejections[-8:])
            dropped = ("\n# OPS DROPPED LAST TURN (the engine rejected these — fix the cause, do "
                       "not re-emit them unchanged):\n" + lines)

        attempt = self._attempts.get(ctx.phase, 1)
        feedback = ""
        if attempt > 1:
            # prefer the EXACT gate reason the engine handed back (GAP 3); fall back to the
            # generic hint only when no reason was recorded.
            why = (f"The gate rejected it — reason: {ctx.gate_feedback}. "
                   if ctx.gate_feedback else
                   "Your previous plan did NOT pass the gate (usually: produces_required was empty, "
                   "min_facts not met, confidence gate unmet, or no refutation). ")
            # role binding (P7 phase-as-data): the symptom-seeding nudge keys on the playbook's
            # entry_phase binding, never on a phase name.
            feedback = (
                f"\n# !! REPLAN NOTICE: this is attempt #{attempt} at the '{ctx.phase}' phase. "
                + why
                + "Do something DIFFERENT this time — do not repeat the same calls. "
                + (f"In {ctx.phase.upper()}, emit the Anomaly node + an onset_value fact NOW. "
                   if ctx.phase == ctx.entry_phase else "")
                + (f"Tools you have already called (data, if any, is already in the graph above): "
                   f"{sorted(self._called)}."))

        return f"""\
{self.catalog_text}

{self.tools_text}
{wired}

# INCIDENT
subject: {ctx.subject.model_dump()}

# CURRENT PHASE: {ctx.phase}
goal: {ctx.goal}
allowed_intents this phase (ABSTRACT categories — fulfil them with the WIRED tools above, not by
  emitting these words as tool names): {spec.allowed_intents}
produces_required (must be non-empty to advance): {spec.produces_required or '(none)'}
GATE to ADVANCE: {gate}{write_hint}{correlated}{steer}{dropped}{feedback}
PROGRESSION RULE: {self.doctrine.progression}

# CURRENT GRAPH — FOCUS SLICE (everything your prior tool calls have discovered, tiered by
# relevance to the symptom: read the full cards for evidence, expand along the FRONTIER for
# targeted evidence, and never re-propose a RULED OUT claim)
{render_focus(ctx.focus)}

# RANKED HYPOTHESES (the hypothesis store so far — engine-earned confidence; `supporting`/
# `refuting` are evidence COUNTS: a leader with refuting=0 has not been challenged yet)
{json.dumps(ctx.hypotheses, default=str, indent=1)}{self._render_projections(ctx)}

Plan this phase. Return ONLY the JSON object."""

    # ── graph-projection reasoning (P7: projections drive reasoning, not fold-and-forget) ──
    def _render_projections(self, ctx: PlanContext) -> str:
        """Engine-computed GOVERNED traversals (graph/tools) targeted at the current
        hypotheses — the planner-side half of the projection→reason→act loop. For the symptom:
        its 1-hop neighbourhood. For each ranked hypothesis root: whether the root is a real
        node, whether/how it connects to the symptom's affected surface (path over the
        governed spine), who breaks if it fails (blast_radius — structural roots only), and
        the leader's 2-hop evidence neighbourhood (walk). Pure reads of the live graph ref;
        hermetic runs without one (self.graph is None) render nothing."""
        g = self.graph
        if g is None or not getattr(g, "nodes", None):
            return ""
        lines: list[str] = []
        focus = ctx.focus.get("focus") if ctx.focus else None
        affected: list[str] = []
        if focus is not None:
            affected = sorted({e.dst for e in g.edges.values()
                               if e.src == focus and e.state == FactState.ACTIVE
                               and e.type == EdgeType.AFFECTS and g.node(e.dst) is not None})
            nb = graph_tools.neighbours(g, focus)
            if nb["count"]:
                lines.append("symptom neighbourhood: " + "; ".join(
                    f"{v['id']} [{v['edge_type']}:{v['direction']}]"
                    for v in nb["neighbours"][:8]))
        ranked = [h for h in (ctx.hypotheses or []) if h.get("root_candidate")]
        for h in ranked[:3]:
            root = str(h["root_candidate"])
            rid = g.id_remaps.get(root, root)
            if g.node(rid) is None:
                lines.append(f"{h.get('id')} root={root!r}: NOT a node in the graph — "
                             "copy the exact id of a real node as root_candidate")
                continue
            entry = f"{h.get('id')} root={rid}:"
            br = graph_tools.blast_radius(g, rid)
            if br["count"]:
                ids = ", ".join(v["id"] for v in br["impacted"][:8])
                entry += f" if it fails, {br['count']} node(s) break [{ids}]"
            else:
                entry += (" no structural dependents recorded (for a change/commit root the"
                          " impact flows through the entity it changed)")
            lines.append(entry)
            for a in affected[:1]:
                if a == rid:
                    continue
                p = graph_tools.path(g, rid, a)
                lines.append("   connection to the symptom's affected node: "
                             + (f"{p['hops']} hop(s): " + " -> ".join(p["nodes"])
                                if p["found"] else
                                f"NO governed path {rid} -> {a} recorded yet"))
        if ranked:
            rid = g.id_remaps.get(str(ranked[0]["root_candidate"]), str(ranked[0]["root_candidate"]))
            if g.node(rid) is not None:
                w = graph_tools.walk(g, rid, max_hops=2, max_nodes=9)
                near = ", ".join(f"{n['id']}@{n['hops']}" for n in w["nodes"] if n["id"] != rid)
                if near:
                    lines.append(f"leader evidence neighbourhood (walk <=2 hops from {rid}): {near}")
        if not lines:
            return ""
        return ("\n\n# GRAPH PROJECTIONS (engine-computed governed traversals — REASON over"
                "\n# them: target evidence where the leader's mechanism predicts it, refute a"
                "\n# rival whose root cannot reach the symptom, expand along the frontier):\n"
                + "\n".join("  " + line for line in lines))

    # ── map LLM JSON -> PlanOutput (reject + repair off-catalog) ───────────────
    def _to_plan_output(self, ctx: PlanContext, raw: dict) -> PlanOutput:
        phase = ctx.phase
        # the LLM output is untrusted: a non-dict top level (JSON array, bare string) is
        # repaired to an EMPTY plan — reject+repair, never a hard crash that would kill
        # the whole live session (INV-7; 2026-07-22 review, finding 5)
        if not isinstance(raw, dict):
            msg = (f"[{phase}] repaired non-dict plan payload "
                   f"({type(raw).__name__}: {str(raw)[:120]!r}) -> empty plan")
            self.repairs.append(msg)
            raw = {}
        trace = PhaseTrace(phase=phase, reasoning=str(raw.get("reasoning", "")),
                           narrative=str(raw.get("narrative", "")), verdict="", raw=raw)

        # calls — drop any intent the layer cannot resolve (non-dict entries included)
        calls: list[CapabilityCall] = []
        for c in raw.get("calls", []) or []:
            if not isinstance(c, dict):
                msg = f"[{phase}] dropped non-dict call entry: {str(c)[:120]!r}"
                trace.repairs.append(msg)
                self.repairs.append(msg)
                continue
            intent = c.get("intent")
            if intent in self.tool_intents:
                calls.append(CapabilityCall(intent=intent, params=(c.get("params") or {})))
                trace.calls.append(intent)
            else:
                msg = f"[{phase}] dropped off-catalog tool intent: {intent!r}"
                trace.repairs.append(msg)
                self.repairs.append(msg)

        # ops — parse each into a typed Operation; drop the unparseable (repair)
        ops: list[Operation] = []
        for o in raw.get("ops", []) or []:
            op, err = self._parse_op(o)
            if op is not None:
                ops.append(op)
                if isinstance(op, ProposeHypothesis):
                    trace.proposed.append(f"{op.hid}:{op.confidence_level.value} root={op.root_candidate}")
                elif isinstance(op, UpdateHypothesis):
                    trace.updated.append(f"{op.hid}->{op.new_status or op.confidence_level}")
                elif isinstance(op, AddNode):
                    trace.nodes.append(op.type.value)
                elif isinstance(op, AddFact):
                    trace.facts.append(f"{op.subject.split(':')[0]}.{op.predicate}")
                elif isinstance(op, AddEdge):
                    trace.edges.append(f"{op.type.value}:{op.src}->{op.dst}")
            else:
                # o may be a non-dict (string/list/None) — the repair record itself must
                # not crash on it (2026-07-22 review, finding 5: the guard's own o.get
                # raised AttributeError and DoS'd the session it exists to protect)
                label = o.get("op") if isinstance(o, dict) else o
                m = f"[{phase}] dropped op {label!r}: {err}"
                trace.repairs.append(m)
                self.repairs.append(m)

        narrative = trace.narrative or trace.reasoning or f"{phase} phase"
        verdict = self._parse_verdict(raw.get("verdict"), narrative,
                                      ctx.tunables.confidence_band)
        trace.verdict = verdict.status.value
        self.traces.append(trace)
        if self.verbose:
            self._log(trace)
        return PlanOutput(phase=ctx.phase, calls=calls, ops=ops, narrative=narrative,
                          verdict=verdict, next_actions=[str(x) for x in (raw.get("next_actions") or [])])

    def _parse_op(self, o):
        if not isinstance(o, dict):   # untrusted payload: repair, never raise (INV-7)
            return None, f"non-dict op payload ({type(o).__name__})"
        try:
            kind = (o.get("op") or "").strip().lower()
            if kind == "add_node":
                return AddNode(type=NodeType(o["type"]), props=o.get("props") or {}), None
            if kind == "add_fact":
                subject, predicate = self._canon(o["subject"]), o["predicate"]
                bad = self._illegal_predicate(subject, predicate)
                if bad is not None:
                    return None, bad
                at = self._dt(o.get("at") or o.get("valid_from"))
                src, lvl, rel = self._belief_channel(o)
                return AddFact(
                    subject=subject, predicate=predicate, value=self._fact_value(o.get("value")),
                    unit=o.get("unit"), valid_from=at, observed_at=self._dt(o.get("observed_at"), at),
                    source=src, confidence_level=lvl, source_reliability=rel,
                ), None
            if kind == "add_event":
                at = self._dt(o.get("at") or o.get("occurred_at"))
                # add_fact's twin shim: models mirror add_fact's `subject` field here (the
                # live grok runs KeyError'd on 'entity' every verify turn until the contract
                # gained an add_event example) — accept the alias, and canon the ref exactly
                # like add_fact does so a cleared event lands on the canonical symptom node.
                entity = o.get("entity") or o.get("subject")
                if not entity:
                    return None, "add_event missing entity/subject"
                return AddEvent(entity=self._canon(entity), type=o["type"], occurred_at=at,
                                observed_at=self._dt(o.get("observed_at"), at),
                                payload=o.get("payload") or {}, source=Source(o.get("source", "llm"))), None
            if kind == "add_edge":
                return AddEdge(
                    type=EdgeType(o["type"]), src=o["src"], dst=o["dst"],
                    origin=Origin(o["origin"]) if o.get("origin") else None,
                    confidence_level=self._level(o.get("confidence_level")),
                    props=o.get("props") or {},
                ), None
            if kind in ("propose_hypothesis", "propose"):
                return ProposeHypothesis(
                    hid=_hid(o["hid"]), statement=o["statement"], root_candidate=o.get("root_candidate"),
                    confidence_level=self._level(o.get("confidence_level")) or ConfidenceLevel.LOW,
                    supporting=[str(x) for x in (o.get("supporting") or [])],
                    refuting=[str(x) for x in (o.get("refuting") or [])],
                    predictions=[str(x) for x in (o.get("predictions") or [])],
                ), None
            if kind in ("update_hypothesis", "update"):
                return UpdateHypothesis(
                    hid=_hid(o["hid"]), new_status=o.get("new_status"),
                    confidence_level=self._level(o.get("confidence_level")),
                    add_supporting=[str(x) for x in (o.get("add_supporting") or [])],
                    add_refuting=[str(x) for x in (o.get("add_refuting") or [])],
                    basis=str(o.get("basis", "")),
                ), None
            if kind == "no_evidence":
                return NoEvidence(intent=str(o["intent"]), scope=str(o.get("scope", "")),
                                  basis=str(o.get("basis", "")), at=self._dt(o.get("at"))), None
            if kind == "retract":
                return Retract(target=str(o["target"]),
                               invalidated_by=(str(o["invalidated_by"])
                                               if o.get("invalidated_by") else None),
                               reason=str(o.get("reason", ""))), None
            # P5's identity graduations, reachable from a LIVE model at last (P6 convergence
            # wiring): the doctrine has advertised merge/retype since the ops shipped, but the
            # parser silently dropped them — the "live parser drops it" bug class.
            if kind == "merge":
                return Merge(provisional_id=str(o["provisional_id"]),
                             canonical_id=str(o["canonical_id"]),
                             reason=str(o.get("reason", ""))), None
            if kind == "retype":
                return Retype(target=str(o["target"]), new_type=NodeType(o["new_type"]),
                              props=o.get("props") or {},
                              reason=str(o.get("reason", ""))), None
            return None, f"unknown op kind {kind!r}"
        except Exception as e:  # any bad field => repair (drop) this op
            return None, f"{type(e).__name__}: {e}"

    def _parse_verdict(self, v: dict | None, narrative: str,
                       band: dict[str, float]) -> PhaseVerdict:
        if isinstance(v, str):
            # a bare-string verdict ("advance") is a status, not an object — repair it
            self.repairs.append(f"coerced bare-string verdict {v!r} -> {{status: ...}}")
            v = {"status": v}
        elif v is not None and not isinstance(v, dict):
            self.repairs.append(
                f"dropped non-dict verdict payload ({type(v).__name__}); using defaults")
            v = None
        v = v or {}
        try:
            status = VerdictStatus(str(v.get("status", "advance")).strip().lower())
        except ValueError:
            status = VerdictStatus.ADVANCE
            self.repairs.append(f"coerced bad verdict status {v.get('status')!r} -> advance")
        # the coarse level maps to a numeric via the playbook's confidence_band tunable
        # (the same band the reducer uses) — no hardcoded band constant in the planner
        level = (str(v.get("confidence_level", "med")).strip().lower())
        value = band.get(level, 0.6)
        basis = str(v.get("basis") or narrative or "llm verdict")[:400] or "llm verdict"
        return PhaseVerdict(status=status, confidence=Confidence(value=value, basis=basis))

    @staticmethod
    def _belief_channel(o: dict) -> tuple[Source, ConfidenceLevel | None, float | None]:
        """Repair a model-authored fact to the Fact belief-channel invariant (R-C4 / §B P0 #3)
        BEFORE it reaches the reducer, which constructs Fact directly and would RAISE (crashing
        the run) rather than reject: an inferred fact (source=llm) carries exactly a confidence;
        any measured source carries exactly a source_reliability. The model routinely omits the
        matching channel — fill the default and null the other so the reducer's Fact is always
        valid (reject+repair, never a hard crash)."""
        try:
            src = Source(str(o.get("source", "llm")).strip().lower())
        except ValueError:
            src = Source.LLM   # unknown source -> treat as the model's own inference
        if src == Source.LLM:
            lvl = None
            try:
                lvl = ConfidenceLevel(str(o.get("confidence_level", "med")).strip().lower())
            except ValueError:
                lvl = ConfidenceLevel.MED
            return src, lvl, None
        rel = o.get("source_reliability")
        rel = float(rel) if isinstance(rel, int | float) else 0.9
        return src, None, min(max(rel, 0.0), 1.0)

    @staticmethod
    def _fact_value(v):
        """Coerce a fact value to the Fact's allowed scalar set (bool|int|float|str|dict|None);
        a list/other is stringified so Fact construction cannot raise on the value type."""
        return v if isinstance(v, bool | int | float | str | dict) or v is None else str(v)

    @staticmethod
    def _illegal_predicate(subject: str, predicate: str) -> str | None:
        """Reject a model-authored fact HERE (repair) so it never becomes a reducer rejection:
          - a fact on a Hypothesis node (prefix `hyp`/`hypothesis`) — belief evidence attaches via
            add_supporting/add_refuting on the hypothesis, NEVER as a fact on the hyp node (and the
            model often mis-ids it as `hypothesis:h1` vs the real `hyp:h1` -> unknown subject);
          - a predicate illegal for the subject's node type (e.g. `degraded` on the Anomaly, which
            takes only onset_value/severity_score — it belongs on the Service).
        Mirrors the reducer's authoritative dictionary check (P2 §2.3): the name is canonicalized
        first (so a vendor spelling like `red_latency_p99` and its canonical `latency_p99` both
        resolve), then checked against `applies_to`. An unknown non-hypothesis prefix is left for
        the reducer. An UNKNOWN NAME is also left for the reducer (P3 §2.4): it is no longer a
        wasted turn — the airlock lands it as a provisional `x.<source>.<native>` assertion, so
        pre-dropping it here would silently erase the discovery signal the airlock exists to
        collect."""
        prefix = subject.split(":", 1)[0]
        if prefix in ("hyp", "hypothesis"):
            return f"fact on a hypothesis node ({subject}) — use add_supporting/add_refuting"
        try:
            nt = NodeType(prefix)
        except ValueError:
            return None
        canonical = dictionary.resolve(None, predicate, None)
        if canonical is None:
            return None                     # unknown name → the reducer QUARANTINES it (P3)
        if not dictionary.applies_to_ok(canonical, nt):
            return f"illegal predicate '{canonical}' on {nt.value} (moved off-node)"
        return None

    @staticmethod
    def _canon(nid: str) -> str:
        """Canonicalize a node id the model authored to the registry's slug form (lowercased,
        spaces/'/'->'-'), so a fact subject typed with the wrong casing/spacing still resolves to
        the node the tools created — reject+repair here, never a reducer rejection. Only the part
        after the 'type:' prefix is slugged; the prefix and the '|' key-joiner are preserved (this
        matches registry.node_id, which slugs each identity key). Idempotent on already-canonical
        ids. Mainly protects the FRAME Anomaly's own onset fact (the one id the model can't yet
        copy from the graph because it is creating the node the same turn)."""
        s = str(nid).strip()
        if ":" not in s:
            return s
        prefix, rest = s.split(":", 1)
        return f"{prefix}:{rest.replace(' ', '-').replace('/', '-').lower()}"

    def _level(self, x) -> ConfidenceLevel | None:
        if not x:
            return None
        try:
            return ConfidenceLevel(str(x).strip().lower())
        except ValueError:
            return None

    def _dt(self, x, default: datetime | None = None) -> datetime:
        if isinstance(x, datetime):
            return x
        if isinstance(x, str) and x:
            try:
                return datetime.fromisoformat(x.replace("Z", "+00:00"))
            except ValueError:
                pass
        return default or self.default_at

    def _log(self, t: PhaseTrace) -> None:
        print(f"  ── {t.phase.upper()} → verdict={t.verdict}")
        print(f"     reasoning: {t.reasoning[:240]}")
        if t.calls:
            print(f"     calls: {t.calls}")
        if t.nodes:
            print(f"     +nodes: {t.nodes}   +facts: {t.facts}   +edges: {len(t.edges)}")
        if t.proposed:
            print(f"     propose: {t.proposed}")
        if t.updated:
            print(f"     update: {t.updated}")
        if t.repairs:
            print(f"     REPAIRS: {t.repairs}")
