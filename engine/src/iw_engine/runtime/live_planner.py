"""live_planner.py — a REAL-LLM Planner behind the same `Planner` Protocol the deterministic
`ScriptedPlanner` implements (principle 11: JUDGMENT is a swappable seam). `plan(ctx)` builds
a prompt from {phase goal + gate, the catalog grammar, the concrete tool list, the current
graph slice, the ranked hypotheses}, asks the LLM for ONE JSON plan, and maps that plan to a
`PlanOutput` (typed ops only — prose is a field, not the channel). Off-catalog output is
rejected+repaired here (invalid enum member, unparseable op, unknown tool) BEFORE it reaches
the reducer, which is the second, authoritative guard.

The LLM never emits free-form graph mutations except through the closed op set:
  calls:[{intent,params}]        -> CapabilityCall  (tool -> data ops via the layer)
  ops:[typed op dicts]           -> AddNode/AddFact/AddEvent/AddEdge/Propose/Update/NoEvidence
  narrative, verdict{status,confidence_level,basis}, next_actions
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..capability.layer import CapabilityCall
from ..domain import registry
from ..domain.common import Confidence
from ..domain.enums import (
    ConfidenceLevel,
    EdgeType,
    NodeType,
    Origin,
    Source,
    VerdictStatus,
)
from ..domain.operations import (
    AddEdge,
    AddEvent,
    AddFact,
    AddNode,
    NoEvidence,
    Operation,
    ProposeHypothesis,
    UpdateHypothesis,
)
from ..domain.phase_result import PhaseVerdict
from .planner import PlanContext, PlanOutput


def _hid(x) -> str:
    """Normalize a hypothesis local id. The graph shows a hypothesis NODE as `hyp:h1`; if the
    model round-trips that displayed id back as the hid, the builder would re-prefix it to
    `hyp:hyp:h1` and fragment the ledger. Strip a leading `hyp:` so `h1` and `hyp:h1` both map
    to the same ledger entry (reject+repair)."""
    s = str(x).strip()
    return s[4:] if s.startswith("hyp:") else s


_ID_KEYS = ("sha", "change_id", "segment_id", "db_id", "signature_hash", "alert_id",
            "incident_id", "anomaly_id", "service_name")


def render_graph_full(graph, *, max_facts: int = 8) -> str:
    """A compact, COMPLETE view of the live workbench graph — every node (id+type+distinguishing
    props+key facts) and edge. The live planner holds a direct graph reference and renders it in
    full here (uncapped, richer per-node formatting) as its primary evidence context, alongside
    the engine's render_slice (which since GAP 5 also hands the full graph). A higher fact cap +
    the props line surface the folded CONTENT (a diff's `DROP INDEX` line, a blame file:line, a
    change_type) the planner must reason over. Engine behaviour is untouched; this is extra
    planner context (live-only)."""
    nlines = []
    for n in graph.nodes.values():
        facts = graph.facts_of(n.id)[:max_facts]
        ftxt = ", ".join(
            f"{f.predicate}={f.value}{('' + f.unit) if f.unit else ''}" for f in facts)
        # surface the discriminating props (type/change_type/file_line/...) the id alone hides
        props = {k: v for k, v in n.props.items()
                 if v is not None and k not in _ID_KEYS and k != "statement"}
        ptxt = ", ".join(f"{k}={v}" for k, v in props.items())
        line = f"  {n.id} [{n.type.value}]"
        if ptxt:
            line += f"  props: {ptxt}"
        if ftxt:
            line += f"  facts: {ftxt}"
        nlines.append(line)
    elines = [f"  {e.type.value}: {e.src} -> {e.dst}"
              + (f" (conf={e.confidence.value})" if e.confidence else "")
              for e in graph.edges.values()]
    if not nlines:
        return "(graph is empty — nothing discovered yet)"
    return "NODES:\n" + "\n".join(nlines) + ("\nEDGES:\n" + "\n".join(elines) if elines else "")


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



# ── the planner ───────────────────────────────────────────────────────────────
_SYSTEM = """\
You are a senior SRE running a disciplined root-cause investigation of a production incident.
A deterministic engine drives you ONE phase at a time. Each turn you: (1) gather evidence by
calling tools, (2) mutate the incident graph ONLY through the closed typed-op grammar,
(3) maintain ranked, evidence-backed hypotheses, and (4) return a verdict that routes the
engine. You reason like a differential diagnostician: change-first, actively try to REFUTE
your leading hypothesis and rule OUT rivals — confirmation is Popperian, not vibes.

HARD RULES
- Use ONLY node/edge types and intents from the provided grammar/tool list. Inventing a
  label, an illegal edge pair, or an unknown tool wastes the turn (it is rejected).
- Graph structure (services, changes, commits, databases, segments, metrics, error signatures)
  comes from TOOL CALLS — prefer `calls`. Emit direct graph `ops` only for: (a) the Anomaly
  symptom node + its onset_value/severity_score facts in FRAME (and, in VERIFY, the recovery
  fact `degraded=false` on the SERVICE — the Anomaly takes ONLY onset_value/severity_score,
  never `degraded`), (b) hypotheses (propose/update), (c) no_evidence null results. Do NOT
  hand-fabricate a service/db/commit/segment/change/error node a tool returns.
- NEVER emit `add_edge`. Every structural edge (depends_on, connects_to, ...) comes from a tool
  and every causal/evidence edge (caused_by, supports, refutes) is DERIVED by the engine from
  your hypothesis's root_candidate + supporting/refuting basis. A hand-authored edge is rejected
  and wastes the turn — encode causation in the hypothesis, not an edge.
- Only emit `add_fact` on the Anomaly (onset_value/severity_score) or a Service (red_errors,
  red_latency_p50, red_latency_p99, degraded). All other facts (pool util, retransmits, diff
  lines, blame, error counts) come from the tools — never author them by hand (a fact whose
  predicate is illegal for the node, or whose subject you mis-copied, is rejected).
- IN FRAME you MUST, in the SAME turn, (1) call the change/alert tools that are wired for this
  incident, AND (2) emit `add_node` for the Anomaly PLUS at least one `add_fact` onset_value on
  it (the wired alert/change tools return nodes+events but NO facts, so the gate's min_facts is
  satisfied ONLY by your Anomaly onset fact). Anomaly onset facts are the symptom you framed;
  their exact value is your best read of the alert.
- A tool's data lands in the graph you see on your NEXT turn — so read the CURRENT GRAPH
  slice carefully; it holds the evidence from everything you called before.
- root_candidate of a hypothesis MUST be a node id copied from the graph slice (the
  initiating change/commit/segment/db), never prose.
- A hypothesis `hid` is a SHORT local id like "h1"/"h2" (NOT "hyp:h1"). The graph shows a
  hypothesis node as "hyp:h1" — to update it, pass hid "h1". Refute a rival by updating its
  hid with new_status "refuted"; do NOT re-propose it under a new id.
- The `root_candidate` is the ROOT (the initiating change/commit/segment/fault) — trace the
  causal chain back to what INITIATED it, not the intermediate mechanism/resource it saturates
  along the way. A saturated resource is often a symptom of an upstream change, not the root.
  GENERAL PRINCIPLE — root at the ACTIONABLE CAUSE you would revert or fix (a change, a commit,
  a config, a security policy/rule, or the load that saturates a limit). NEVER root at a resource
  that is merely the CONDUIT the traffic passes through or the SYMPTOM it shows: if a resource on
  the path is itself HEALTHY, it is a conduit, not the root. Ask "what is the ONE thing I would
  change to fix this?" — that is the root.
  Rooting convention by fault class: an application code defect roots at the CODE_COMMIT the
  error/blame resolves to (the deploy that shipped it is the vehicle, not the root); a schema/
  index/DB-migration or config change roots at the CHANGE_EVENT that made it (NOT the database
  whose pool it later saturates — the pool is the mechanism); a TRANSPORT fault — the link ITSELF
  is degraded (packet_loss > 0, retransmits climbing) — roots at the NETWORK_SEGMENT; but a
  FIREWALL / SECURITY-POLICY block — the link is HEALTHY (packet_loss ~ 0, no retransmits) yet
  traffic is cleanly DENIED — roots at the POLICY CHANGE that tightened it (or the FIREWALL_RULE
  it modified), NEVER the healthy segment the denied traffic merely crosses. Distinguish the two
  by the link's health: degraded link → segment; clean denials on a healthy link → the policy.
  Copy that node's exact id from the graph as root_candidate.
- Advance only when the phase's GATE is satisfied. In investigate you must reach a
  high-confidence leader AND have ruled out a rival (or challenged the leader). A leader is
  HIGH confidence once the root resource shows the fault DIRECTLY (the blame line on the commit,
  the DROP INDEX in the diff, the retransmits/packet-loss on the segment, the saturation on the
  host) AND the rival is ruled out — you do NOT need a separate change ticket to confirm. When
  you have that, set the leader to status "supported" at "high" and ADVANCE; do not keep
  re-investigating for more corroboration (repeating the same query is a wasted turn).
- In verify, once the symptom has cleared, set the leader status "confirmed" and emit verdict
  `advance` (the engine routes verify -> close); reserve verdict `done` for the CLOSE phase only.
- The phase's `allowed_intents` are ABSTRACT CATEGORIES, not tool names. Always call CONCRETE
  tools from the AVAILABLE TOOLS list. If a set of calls added NO new nodes/facts to the graph
  last turn, that tool was not wired for this incident — switch to a DIFFERENT wired tool; never
  repeat a call that returned nothing.
- In HYPOTHESIZE always propose the leading hypothesis AND at least one distinct RIVAL rooted at
  a different node. In INVESTIGATE actively gather evidence that could REFUTE a hypothesis and
  set the loser's status to "refuted" — the gate will not let you advance until a rival is ruled
  out (or the leader is challenged with refuting evidence). Do not loop re-supporting one idea.

OUTPUT: a single JSON object, no markdown, exactly:
{
  "reasoning": "your differential reasoning for THIS phase (2-5 sentences)",
  "calls": [{"intent": "<tool intent>", "params": {}}],
  "ops": [
    {"op":"add_node","type":"anomaly","props":{"anomaly_id":"ANOM-1"}},
    {"op":"add_fact","subject":"anomaly:anom-1","predicate":"onset_value","value":5200,"unit":"ms","source":"prometheus","at":"2026-07-19T14:05:00+00:00"},
    {"op":"propose_hypothesis","hid":"h1","statement":"...","root_candidate":"<node id>","confidence_level":"med"},
    {"op":"update_hypothesis","hid":"h2","new_status":"refuted","basis":"...","add_refuting":[]},
    {"op":"no_evidence","intent":"healthrule_violations","scope":"<node id>","basis":"...","at":"2026-07-19T14:20:00+00:00"}
  ],
  "narrative": "concise phase narrative for the journal",
  "verdict": {"status":"advance|repeat|backtrack|blocked|done","confidence_level":"low|med|high","basis":"why this verdict"},
  "next_actions": ["what the next phase should do"]
}
valid op kinds: add_node, add_fact, add_event, add_edge, propose_hypothesis, update_hypothesis, no_evidence.
valid sources: prometheus, splunk, appd, servicenow, cmdb, ocp, artifactory, git, llm, human, engine.
valid hypothesis statuses: proposed, investigating, supported, confirmed, refuted, superseded."""


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
                 default_at: datetime | None = None, verbose: bool = True):
        self.client = client
        self.catalog_text = catalog_text
        self.tools_text = tools_text
        self.tool_intents = set(tool_intents)
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
        self._attempts[ctx.phase.value] = self._attempts.get(ctx.phase.value, 0) + 1
        user = self._build_prompt(ctx)
        raw = self.client.complete_json(_SYSTEM, user)
        out = self._to_plan_output(ctx, raw)
        self._called.update(c.intent for c in out.calls)
        return out

    # ── prompt ────────────────────────────────────────────────────────────────
    def _build_prompt(self, ctx: PlanContext) -> str:
        spec = ctx.phase_spec
        gate_bits = []
        if spec.gate.min_facts:
            gate_bits.append(f"min_facts>={spec.gate.min_facts}")
        if spec.gate.require_confidence_gate:
            gate_bits.append(f"leading confidence>={ctx.tunables.confidence_gate}")
        if spec.gate.require_refutation:
            gate_bits.append("a rival refuted OR the leader challenged")
        gate = "; ".join(gate_bits) or "(none — just produce the required fields)"

        wired = ""
        if self.available_sources is not None:
            wired = ("\n# TOOLS WIRED FOR THIS INCIDENT (ONLY these return data; any other\n"
                     "# tool resolves but returns EMPTY — do not waste calls on them):\n#   "
                     + ", ".join(sorted(self.available_sources)))

        remediate_hint = ""
        if ctx.phase.value == "remediate":
            remediate_hint = (
                "\n# REMEDIATE: propose the concrete fix as an `apply_remediation` capability CALL "
                "(a WRITE) — e.g. calls:[{intent:'apply_remediation', params:{action:'<the reversible "
                "fix>', reversible:true}}]. This WRITE opens the human approval gate; do NOT just "
                "describe the fix in prose or you will skip the human-in-the-loop approval.")

        steer = ""
        if ctx.messages:
            # operator steering from the two-way chat (obs 2) — the human in the loop. Recent last.
            lines = "\n".join(f"#   - {m.get('text', '')}" for m in ctx.messages[-6:])
            steer = ("\n# OPERATOR STEERING (the human investigating with you said this — weigh it "
                     "heavily, it may redirect your hypothesis or tool choice):\n" + lines)

        attempt = self._attempts.get(ctx.phase.value, 1)
        feedback = ""
        if attempt > 1:
            # prefer the EXACT gate reason the engine handed back (GAP 3); fall back to the
            # generic hint only when no reason was recorded.
            why = (f"The gate rejected it — reason: {ctx.gate_feedback}. "
                   if ctx.gate_feedback else
                   "Your previous plan did NOT pass the gate (usually: produces_required was empty, "
                   "min_facts not met, confidence gate unmet, or no refutation). ")
            feedback = (
                f"\n# !! REPLAN NOTICE: this is attempt #{attempt} at the '{ctx.phase.value}' phase. "
                + why
                + "Do something DIFFERENT this time — do not repeat the same calls. "
                + ("In FRAME, emit the Anomaly node + an onset_value fact NOW. "
                   if ctx.phase.value == "frame" else "")
                + (f"Tools you have already called (data, if any, is already in the graph above): "
                   f"{sorted(self._called)}."))

        return f"""\
{self.catalog_text}

{self.tools_text}
{wired}

# INCIDENT
subject: {ctx.subject.model_dump()}

# CURRENT PHASE: {ctx.phase.value}
goal: {ctx.goal}
allowed_intents this phase (ABSTRACT categories — fulfil them with the WIRED tools above, not by
  emitting these words as tool names): {spec.allowed_intents}
produces_required (must be non-empty to advance): {spec.produces_required or '(none)'}
GATE to ADVANCE: {gate}{remediate_hint}{steer}{feedback}
PROGRESSION RULE: set verdict=advance as soon as this phase's produces_required + gate are
  satisfied — do NOT loop re-gathering evidence. TRIAGE just decides mitigate-vs-investigate and
  names the suspect; DEEP evidence (diffs, traces, blame, refuting a rival) belongs in INVESTIGATE.
  Only verdict=repeat when a REQUIRED output is genuinely still missing this phase.

# CURRENT GRAPH (everything your prior tool calls have discovered — read it for evidence)
{render_graph_full(self.graph) if self.graph is not None else json.dumps(ctx.graph_view, default=str, indent=1)}

# RANKED HYPOTHESES (the ledger so far)
{json.dumps(ctx.hypotheses, default=str, indent=1)}

Plan this phase. Return ONLY the JSON object."""

    # ── map LLM JSON -> PlanOutput (reject + repair off-catalog) ───────────────
    def _to_plan_output(self, ctx: PlanContext, raw: dict) -> PlanOutput:
        phase = ctx.phase.value
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
                return AddEvent(entity=o["entity"], type=o["type"], occurred_at=at,
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
        An unknown non-hypothesis prefix is left for the reducer's authoritative check."""
        prefix = subject.split(":", 1)[0]
        if prefix in ("hyp", "hypothesis"):
            return f"fact on a hypothesis node ({subject}) — use add_supporting/add_refuting"
        try:
            nt = NodeType(prefix)
        except ValueError:
            return None
        if not registry.predicate_allowed(nt, predicate):
            return f"illegal predicate '{predicate}' on {nt.value} (moved off-node)"
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
