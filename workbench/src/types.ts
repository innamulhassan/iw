// Shape of the static investigation bundle produced by the engine.
// Kept intentionally permissive on string-union fields (with a trailing
// `| string` fallback) since a real engine run may emit node/edge/status
// values beyond the ones observed in the demo fixture.

export type Phase =
  | "frame"
  | "triage"
  | "hypothesize"
  | "investigate"
  | "remediate"
  | "verify"
  | "close";

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
  where?: string | null; // spatial/context W (obs 7)
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

export interface JournalEntry {
  seq: number;
  phase: Phase | string;
  actor: string;
  narrative: string;
  refs: JournalRefs;
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
  chain: unknown[];
}

export interface Postmortem {
  subject?: Subject;
  outcome?: Outcome;
  root_cause: PostmortemRootCause;
  ruled_out: { statement: string; basis: string }[];
  contributing: unknown[];
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

export type SessionState = "running" | "suspended" | "closed";

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
  where?: string | null;
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
export interface SessionStateEvent extends EventBase {
  type: "session_state";
  state: SessionState;
  phase: string | null;
  verdict?: string;
  outcome?: string;
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
  | UserMessageEvent
  | SessionErrorEvent
  | SessionStateEvent;

/** GET /sessions/{id} — export_bundle shape plus the session envelope. */
export interface Snapshot extends InvestigationBundle {
  session_id: string;
  state: SessionState;
  pending_gate: GateOpenedEvent | null;
  messages: { seq: number; text: string; at: string; kind: string }[];
  events: SessionEvent[];
}
