// Shape of the static investigation bundle produced by the engine.
// Kept intentionally permissive on string-union fields (with a trailing
// `| string` fallback) since a real engine run may emit node/edge/status
// values beyond the ones observed in the demo fixture.

// P7 — the 5-phase algebra (playbooks/incident.yaml is the source of truth). The phase VOCAB is
// data-driven (M22): the UI stepper reads the served `phase_rail`, never a hardcoded list, so a
// playbook with different phases needs no UI edit — the union below documents the incident phases
// but keeps the `| string` fallback (a run may emit any playbook-declared phase id).
export type Phase =
  | "frame"
  | "investigate"
  | "act"
  | "verify"
  | "close"
  | string;

/** One rung of the served phase rail (M22): the playbook-declared phase id + whether it is a
 *  `focus` phase (always shown) vs greyed-until-reached. Derived by the engine from the playbook's
 *  writes_allowed role binding, so the stepper needs no hardcoded ALL_PHASES/ACTIVE. */
export interface PhaseRailItem {
  id: string;
  focus: boolean;
}

/** The engine's canonical label dictionary (M25) — served so the UI stops re-authoring the vocab.
 *  `predicates`/`relations` map a canonical name to a default humanized label; `intents` maps each
 *  capability intent to its capability purpose. The UI layers its own curated labels as overrides
 *  and falls back to a de-underscored raw string, so a new engine vocab item is labelled by default
 *  (drift-prevention). NB: the graph LANE layout (tiers.ts) is UI presentation, not served here. */
export interface InvestigationDictionary {
  predicates: Record<string, string>;
  relations: Record<string, string>;
  intents: Record<string, string>;
}

export type Outcome = "resolved" | "mitigated" | "open" | string;

export interface Subject {
  domain: string;
  id: string;
  kind: string;
}

export interface GraphNode {
  id: string;
  type: string;
  props: Record<string, unknown>;
  // provenance projection (obs 1/5): origin = the ServiceNow incident under investigation (#1);
  // source/first_source = which capability observed it; first_seen/last_seen = when.
  origin?: boolean;
  source?: string | null;
  first_source?: string | null;
  first_seen?: string | null;
  last_seen?: string | null;
}

export type EdgeOrigin = "declared" | "discovered" | "inferred" | string;

export interface GraphEdge {
  id: string;
  type: string;
  src: string;
  dst: string;
  origin: EdgeOrigin;
  confidence: number | null;
  source?: string | null; // WHO established the relation (obs 7)
  established?: string | null; // WHEN the relation was established
  state?: string;
  valid_to?: string | null;
  invalidated_by?: number | null;
  provisional?: boolean; // P3 airlock — emitted ONLY when true; renders as tentative
}

export type FactState = "active" | "superseded" | "retracted" | string;

export interface GraphFact {
  id: string;
  subject: string;
  predicate: string;
  value: unknown;
  unit: string | null;
  at: string;
  observed_at?: string | null; // WHEN we learned it (transaction time)
  valid_to: string | null;
  source: string;
  source_native_name?: string | null; // the provider's own name for the field (P3 airlock)
  state: FactState;
  provisional?: boolean; // P3 airlock — quarantined/open-vocabulary knowledge, tentative
}

export interface GraphEvent {
  id: string;
  entity: string;
  type: string;
  at: string;
  payload: Record<string, unknown>;
  source: string;
  source_native_name?: string | null;
  state?: string;
  invalidated_by?: number | null;
  provisional?: boolean; // P3 airlock — tentative until promoted
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  facts: GraphFact[];
  events: GraphEvent[];
}

export type HypothesisStatus =
  | "confirmed"
  | "refuted"
  | "supported"
  | "proposed"
  | string;

/** One link in a hypothesis's causal chain (engine ChainLink). */
export interface ChainLink {
  kind: string; // event | fact | change
  ref: string; // EventId | FactId | NodeId(ChangeEvent) — clickable
  ts: string;
  role: string; // cause | condition | effect
  note?: string | null;
}

export interface HypothesisItem {
  id: string;
  statement: string;
  status: HypothesisStatus;
  /** The ENGINE-EARNED weighted evidence score (P4) — the LLM's band survives only as the
   *  prior inside it and as the `basis` text. */
  confidence: number;
  basis: string;
  root_candidate: string | null;
  supporting: string[];
  refuting: string[];
  chain: ChainLink[];
}

/** The airlock's promotion counters (P3 step 5) — the discovery signal telling a human WHICH
 *  core-registry edit to make. Counted by the engine, surfaced here, never auto-applied. */
export interface DiscoveryCounters {
  class_hints: Record<string, number>;
  quarantined_names: Record<string, number>;
}

/** One reducer rejection derived from the journaled deltas (P3 step 2 — bounded repair):
 *  what evidence was WITHHELD, in which phase, and why. */
export interface RejectionItem {
  seq: number;
  phase: string | null;
  op_index: number;
  op_kind: string;
  reason: string;
}

export interface JournalRefs {
  nodes: string[];
  edges: string[];
  facts: string[];
  events: string[];
  hypotheses: string[];
}

// The bundle now serves EVERY journal kind with its kind + ts + full per-kind fields (the
// COMPOSABLE record: UI, audit and the fold read the ONE shape). Fields beyond the original
// {seq, phase, actor, narrative, refs} are optional and per-kind — a phase entry has goal/
// verdict/refs, a plan has available/plan_calls/plan_ops, an invocation has intent/provider/
// outcome/op_count, a gate_opened has actions/hypothesis/evidence, etc.
export interface JournalEntry {
  seq: number;
  kind?: string; // phase | plan | invocation | gate_opened | gate_decision | message | lifecycle | step
  ts?: string | null;
  phase: Phase | string | null;
  actor: string;
  narrative?: string; // reasoning / the WHY (on an invocation)
  refs?: JournalRefs; // phase entries only
  // phase
  goal?: string;
  next_actions?: string[];
  verdict?: string;
  // plan — the planner's PLAN + the TOOLS AVAILABLE (its access surface)
  available?: string[];
  plan_calls?: string[];
  plan_ops?: string[];
  // invocation — one tool call in full
  intent?: string;
  provider?: string;
  params?: Record<string, unknown>;
  effect?: string;
  outcome?: string;
  op_count?: number;
  blocked?: boolean;
  reason?: string | null;
  served_by?: string | null; // the transport that SERVED it (mock|scenario|mcp|rest) — M1
  binding?: string | null; // the adapter's declared Binding (mcp|rest|a2a) — M1
  // gate_opened — the write-gate question
  gate_id?: string;
  actions?: GateAction[];
  hypothesis?: string | null; // the serving hypothesis id
  evidence?: string[]; // supporting fact ids
  // gate_decision / message / lifecycle
  decision?: string;
  source?: string | null;
  action?: Record<string, unknown>;
  observation?: Record<string, unknown>;
  event?: string; // lifecycle event name (started/resumed/closed/…)
  // phase_review / review_decision — the between-phases DIRECTION review + its answer
  review_id?: string;
  to_phase?: string; // the phase the review proposes advancing to
  facts?: string[]; // ids the reviewed phase discovered (phase_review)
  nodes?: string[]; // ids the reviewed phase discovered (phase_review)
}

export interface PostmortemNarrativeEntry {
  seq: number;
  phase: string;
  text: string;
}

export interface PostmortemTimelineEntry {
  at: string;
  entity: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface PostmortemRootCause {
  statement: string;
  root_candidate: string | null;
  confidence: number;
  chain: ChainLink[];
}

/** One surviving contributing factor (an alive-but-not-confirmed hypothesis). */
export interface PostmortemContributing {
  statement: string;
  confidence: number;
}

export interface Postmortem {
  subject?: Subject;
  outcome?: Outcome;
  /** null when the investigation closed WITHOUT a confirmed root (a `mitigated` outcome) — the
   *  engine's render_postmortem returns None there, so the card shows "no confirmed root cause". */
  root_cause: PostmortemRootCause | null;
  ruled_out: { statement: string; basis: string }[];
  contributing: PostmortemContributing[];
  timeline: PostmortemTimelineEntry[];
  narrative: PostmortemNarrativeEntry[];
}

export interface InvestigationBundle {
  subject: Subject;
  outcome: Outcome;
  phases: Phase[];
  graph: Graph;
  /** In the ENGINE's ranked() order — the UI renders this order, never re-sorts. */
  hypotheses: HypothesisItem[];
  journal: JournalEntry[];
  rejections?: RejectionItem[]; // evidence withheld this run ("ops dropped")
  discovery?: DiscoveryCounters; // "the system keeps seeing unknown X"
  postmortem: Postmortem;
}

// ── The interactive session surface (server.py / runtime/session.py) ──────────────

// "awaiting_review" = paused at a between-phases DIRECTION review (the phase-review gate — owner
// 2026-07-23): the agent finished a phase and asks the human to approve advancing to the next one.
export type SessionState = "running" | "suspended" | "awaiting_review" | "closed";

/** One runnable incident on the start selector (GET /catalog). */
export interface CatalogItem {
  id: string;
  title: string;
  layer: string;
  domain: string;
  kind: string;
}

/** One row of the incident list (GET /sessions). */
export interface SessionListItem {
  id: string;
  subject: Subject;
  state: SessionState;
  outcome: Outcome;
}

/** The proposed write action inside an open gate. */
export interface GateAction {
  intent: string;
  params: Record<string, unknown>;
  provider: string;
  effect: string;
  summary: string;
}

export interface GateHypothesis {
  id: string;
  statement: string;
  status: string;
  confidence: number;
  root_candidate: string | null;
}

export interface GateEvidence {
  id: string;
  subject?: string;
  predicate?: string;
  value?: unknown;
  unit?: string | null;
  source?: string;
  resolved?: boolean;
}

// ── Ordered event stream (SSE / GET events) — each event carries a `seq` ───────────

interface EventBase {
  seq: number;
  ts: string;
}

export interface PhaseStartedEvent extends EventBase {
  type: "phase_started";
  phase: string;
}
export interface ReasoningEvent extends EventBase {
  type: "reasoning";
  phase: string;
  narrative: string;
}
/** The boundary-honesty outcome of an invocation (P3 step 1): `data` (ops folded) ·
 *  `empty` (an HONEST clean no-data read) · `error` (the call FAILED — no evidentiary
 *  weight, never "no data") · `blocked` (no approved gate). The UI must never infer
 *  "clean" from op_count == 0 alone. */
export type InvocationOutcome = "data" | "empty" | "error" | "blocked" | string;

export interface CapabilityCallEvent extends EventBase {
  type: "capability_call";
  intent: string;
  provider: string;
  effect: string;
  op_count: number;
  outcome?: InvocationOutcome;
  blocked: boolean;
  reason: string | null;
  kind?: string; // tool | workflow | llm (obs 9 — tool-vs-workflow)
  started_at?: string | null; // WHEN the call ran
  duration_ms?: number | null; // HOW LONG it took
  params?: Record<string, unknown>; // the query that went IN
  summary?: string; // one-line result that came OUT
  served_by?: string | null; // the transport that SERVED it (mock|scenario|mcp|rest) — M1
  binding?: string | null; // the adapter's declared Binding (mcp|rest|a2a) — M1
}
export interface GraphDeltaNode {
  id: string;
  type: string;
  created_by: number;
  origin?: boolean; // the ServiceNow incident under investigation → #1 (obs 1)
}
export interface GraphDeltaEdge {
  id: string;
  type: string;
  src: string;
  dst: string;
  origin: string;
  source?: string | null;
  established?: string | null;
  provisional?: boolean;
}
export interface GraphDeltaFact {
  id: string;
  subject: string;
  predicate: string;
  value: unknown;
  unit?: string | null;
  source?: string; // WHO (obs 7) — no longer blanked on the live stream
  observed_at?: string | null;
  at?: string;
  provisional?: boolean;
}
export interface GraphDeltaEvent {
  id: string;
  entity: string;
  type: string;
  provisional?: boolean;
}
export interface GraphDeltaEventMsg extends EventBase {
  type: "graph_delta";
  nodes: GraphDeltaNode[];
  edges: GraphDeltaEdge[];
  facts: GraphDeltaFact[];
  events: GraphDeltaEvent[];
}
export interface HypothesisDeltaItem {
  id: string;
  action: string;
  status: string | null;
  confidence: number | null;
  basis: string;
  statement?: string;
  root_candidate?: string | null;
  supporting?: string[];
  refuting?: string[];
}
export interface HypothesesDeltaEvent extends EventBase {
  type: "hypotheses_delta";
  hypotheses: HypothesisDeltaItem[];
}
export interface GateOpenedEvent extends EventBase {
  type: "gate_opened";
  gate_id: string;
  phase: string;
  reasoning: string;
  actions: GateAction[];
  hypothesis: GateHypothesis | null;
  evidence: GateEvidence[];
}

/** Counts of what a phase discovered — surfaced in the phase-review summary. */
export interface PhaseReviewDiscovered {
  facts: number;
  nodes: number;
  events: number;
  edges: number;
  hypotheses: number;
}

/** The between-phases DIRECTION review (owner 2026-07-23): the agent finished `phase` and asks the
 *  human to approve advancing to `to_phase`. The summary body (goal + narrative + discovered counts
 *  + the leading hypothesis), NOT a proposed write — decided approve/refine/deny on the same card. */
export interface PhaseReviewOpenedEvent extends EventBase {
  type: "phase_review_opened";
  review_id: string;
  phase: string; // the phase that just completed
  to_phase: string; // the phase the engine proposes to advance to
  summary: string; // "‘frame’ is complete — proposing to advance to ‘investigate’."
  goal?: string;
  narrative?: string;
  verdict?: string;
  discovered?: PhaseReviewDiscovered;
  hypothesis: GateHypothesis | null;
  facts?: string[];
  nodes?: string[];
}

/** The human's phase-review answer — approve (advance) · refine (re-run with a steer) · deny (halt). */
export interface PhaseReviewDecisionEvent extends EventBase {
  type: "phase_review_decision";
  review_id: string;
  decision: string;
  actor: string;
  source: string;
  reason: string;
  phase: string;
  to_phase: string;
}
export interface SessionStateEvent extends EventBase {
  type: "session_state";
  state: SessionState;
  phase: string | null;
  verdict?: string;
  outcome?: string;
  cause?: string; // on a terminal (closed) state: finished | exhausted | error | denied (M17)
}
/** The human's write-gate answer — WHO decided (actor + Source.HUMAN), and how. */
export interface GateDecisionEvent extends EventBase {
  type: "gate_decision";
  gate_id: string;
  decision: string;
  actor: string;
  source: string;
  reason: string;
  phase: string;
}
/** An operator turn in the two-way chat (obs 2) — steering while running, an answer while suspended. */
export interface UserMessageEvent extends EventBase {
  type: "user_message";
  text: string;
  kind: string; // steer | answer
  actor: string;
  source: string; // "human"
  phase: string | null;
}
/** A live drive failed mid-run (LLM/transport error). */
export interface SessionErrorEvent extends EventBase {
  type: "session_error";
  message: string;
}

export type SessionEvent =
  | PhaseStartedEvent
  | ReasoningEvent
  | CapabilityCallEvent
  | GraphDeltaEventMsg
  | HypothesesDeltaEvent
  | GateOpenedEvent
  | GateDecisionEvent
  | PhaseReviewOpenedEvent
  | PhaseReviewDecisionEvent
  | UserMessageEvent
  | SessionErrorEvent
  | SessionStateEvent;

/** GET /sessions/{id} — export_bundle shape plus the session envelope. */
export interface Snapshot extends InvestigationBundle {
  session_id: string;
  state: SessionState;
  /** The full declared phase rail (M22) — playbook context the stepper renders from, not a
   *  hardcoded list. Optional: an older snapshot without it falls back to the reached phases. */
  phase_rail?: PhaseRailItem[];
  /** The engine's canonical label dictionary (M25) — the UI reads vocab labels from here (with its
   *  curated maps as overrides). Optional: an older snapshot without it keeps the local labels. */
  dictionary?: InvestigationDictionary;
  pending_gate: GateOpenedEvent | null;
  pending_review: PhaseReviewOpenedEvent | null;
  messages: { seq: number; text: string; at: string; kind: string }[];
  events: SessionEvent[];
}
