// The live investigation store — a reducer that mirrors the engine's ordered event stream
// (phase_started · reasoning · capability_call · graph_delta · hypotheses_delta · gate_opened ·
// session_state) into the shape the panes render. The engine is the single source of truth:
// nothing here is invented, every node/fact/hypothesis is upserted from a delta the engine
// emitted. Cold-load seeds full detail from the snapshot bundle; SSE deltas then grow it live.
import type {
  DiscoveryCounters,
  GateOpenedEvent,
  GraphEdge,
  GraphEvent,
  GraphFact,
  HypothesisItem,
  JournalEntry,
  PhaseReviewOpenedEvent,
  RejectionItem,
  SessionEvent,
  SessionState,
  Snapshot,
  Subject,
} from "../types";
import type { GateDecision } from "./api";

export interface LiveNode {
  id: string;
  type: string;
  props: Record<string, unknown>;
  created_by: number; // creation-order seq → the visible number badge
  origin?: boolean; // the ServiceNow incident under investigation → #1 (obs 1)
  source?: string | null; // where-fetched-from (obs 5)
  first_source?: string | null;
  first_seen?: string | null;
  last_seen?: string | null;
}

export interface ToolCall {
  seq: number;
  intent: string;
  provider: string;
  effect: string;
  op_count: number;
  /** Boundary honesty (P3): data · empty (clean no-data) · error (a FAILED call — not
   *  "no data") · blocked. Absent only on pre-P3 recorded streams. */
  outcome?: string;
  blocked: boolean;
  reason: string | null;
  kind?: string; // tool | workflow | llm (obs 9)
  startedAt?: string | null; // WHEN it ran
  durationMs?: number | null; // HOW LONG it took
  params?: Record<string, unknown>; // the query IN
  summary?: string; // the result OUT
}

/** An operator turn in the two-way chat (obs 2). */
export interface UserMsg {
  seq: number;
  text: string;
  kind: string; // steer | answer
  actor: string;
  phase: string | null;
}

/** A cross-pane selection (obs 8): clicking a fact in the hypotheses highlights that node + fact in
 *  the graph, and vice-versa. `id` is a node id or fact id per `kind`. */
export interface Selection {
  kind: "node" | "fact";
  id: string;
}

/** One hypotheses movement observed during a phase (a hypothesis proposed / supported / refuted). */
export interface HypothesisMove {
  id: string;
  action: string;
  status: string | null;
  basis: string;
}

/** What a phase OBSERVED — the graph it grew (facts gathered, nodes/edges/events discovered)
 *  and the beliefs it moved. Accumulated from the same graph_delta / hypotheses_delta the engine
 *  emitted, so the journal can show the full per-phase sequence, not just the summary. */
export interface TurnObs {
  factIds: string[];
  nodeIds: string[];
  edgeIds: string[];
  eventIds: string[];
  hypotheses: HypothesisMove[];
}

function emptyObs(): TurnObs {
  return { factIds: [], nodeIds: [], edgeIds: [], eventIds: [], hypotheses: [] };
}

/** The planner's authored PLAN for a phase — journaled, never streamed (the live SSE stream
 *  carries reasoning + graph/hypothesis deltas only). The tools it had AVAILABLE (its access
 *  surface), the capability intents it DECIDED to call, and the direct graph/hypothesis ops it
 *  authored — so the chat can show HOW the agent intended to use the graph + hypothesis tools,
 *  not just what it reasoned. */
export interface TurnPlan {
  available: string[]; // tools available that phase (PhaseSpec.allowed_intents)
  plannedCalls: string[]; // capability intents it decided to call
  plannedOps: string[]; // direct graph/hypothesis ops it authored (AddNode, ProposeHypothesis, …)
}

/** One chat turn = the agent's work in one phase: its OBJECTIVE (the journaled phase goal), the
 *  PLAN it authored, its reasoning, the tool calls it made, what it OBSERVED (facts/nodes/edges/
 *  events + hypothesis moves), any reducer REJECTIONS this phase incurred, and the write-gate
 *  (when this phase opened one). objective/plan/rejections are threaded from the JOURNAL — the
 *  live event stream doesn't carry them — so both folds enrich to the identical shape. */
export interface Turn {
  key: number; // the phase_started seq (live) / phase-entry seq (reopen) — stable react key
  phase: string;
  objective: string; // the phase GOAL — one concise line; "" until the journal lands it
  reasoning: string;
  plan?: TurnPlan; // the access surface + authored plan (absent on a bare live step)
  calls: ToolCall[];
  obs: TurnObs;
  rejections: RejectionItem[]; // reducer rejections attributed to THIS phase (evidence withheld)
  gateId?: string;
  reviewId?: string; // the phase-review opened when this phase completed (owner 2026-07-23)
}

/** Build a bare turn with the uniform enriched shape — used by BOTH folds so objective/plan/
 *  rejections always exist (empty until enrichTurnsFromJournal threads the journal onto them). */
function newTurn(key: number, phase: string, reasoning = ""): Turn {
  return { key, phase, objective: "", reasoning, calls: [], obs: emptyObs(), rejections: [] };
}

export interface Decision {
  decision: GateDecision;
  reason?: string;
  actor?: string; // WHO answered the gate (from the engine's gate_decision event)
  source?: string; // provenance — "human" on an operator decision
}

export interface LiveState {
  sessionId: string | null;
  subject: Subject | null;
  state: SessionState | null;
  outcome: string;
  nodes: Record<string, LiveNode>;
  edges: Record<string, GraphEdge>;
  facts: Record<string, GraphFact>;
  events: Record<string, GraphEvent>;
  hypotheses: Record<string, HypothesisItem>;
  /** Hypothesis ids in the ENGINE's ranked() order (from the bundle) — the UI renders THIS
   *  order, never a client-side re-sort. Delta-born ids append until the next snapshot merge. */
  hypothesisOrder: string[];
  discovery: DiscoveryCounters; // airlock promotion counters ("unknown X keeps recurring")
  rejections: RejectionItem[]; // evidence withheld this run (bounded-repair signal)
  turns: Turn[];
  messages: UserMsg[]; // operator chat turns (obs 2), interleaved with turns by seq
  gate: GateOpenedEvent | null; // the currently-open write-gate, or null
  gates: Record<string, GateOpenedEvent>; // every gate ever opened, by gate_id
  decisions: Record<string, Decision>; // gate_id → the operator's decision
  // the phase-review gate (owner 2026-07-23) — parallels gate/gates/decisions so the two
  // suspend surfaces never collide (a distinct slot + a distinct AWAITING_REVIEW state).
  review: PhaseReviewOpenedEvent | null; // the currently-open phase-review, or null
  reviews: Record<string, PhaseReviewOpenedEvent>; // every phase-review ever opened, by review_id
  reviewDecisions: Record<string, Decision>; // review_id → the operator's direction decision
  phasesRun: string[]; // phases reached, in order (unique)
  error: string | null; // a live drive failure, surfaced in the chat
  lastSeq: number;
}

export function emptyState(): LiveState {
  return {
    sessionId: null,
    subject: null,
    state: null,
    outcome: "open",
    nodes: {},
    edges: {},
    facts: {},
    events: {},
    hypotheses: {},
    hypothesisOrder: [],
    discovery: { class_hints: {}, quarantined_names: {} },
    rejections: [],
    turns: [],
    messages: [],
    gate: null,
    gates: {},
    decisions: {},
    review: null,
    reviews: {},
    reviewDecisions: {},
    phasesRun: [],
    error: null,
    lastSeq: 0,
  };
}

export type StoreAction =
  | { kind: "reset" }
  | { kind: "seed"; snapshot: Snapshot }
  | { kind: "events"; events: SessionEvent[] }
  | { kind: "mergeDetail"; snapshot: Snapshot }
  | { kind: "decision"; gateId: string; decision: GateDecision; reason?: string }
  | { kind: "reviewDecision"; reviewId: string; decision: GateDecision; reason?: string };

export function reduce(state: LiveState, action: StoreAction): LiveState {
  switch (action.kind) {
    case "reset":
      return emptyState();
    case "seed":
      return seed(action.snapshot);
    case "events":
      return applyEvents(state, action.events);
    case "mergeDetail":
      return mergeDetail(state, action.snapshot);
    case "decision": {
      const decisions = { ...state.decisions, [action.gateId]: { decision: action.decision, reason: action.reason } };
      return { ...state, decisions, gate: null };
    }
    case "reviewDecision": {
      // optimistic: record the direction decision + clear the open review (parallels "decision")
      const reviewDecisions = { ...state.reviewDecisions, [action.reviewId]: { decision: action.decision, reason: action.reason } };
      return { ...state, reviewDecisions, review: null };
    }
  }
}

// ── reopen: rebuild the conversation from the JOURNAL — the durable record ──────────
// A disk-reopened investigation carries no live event stream (events === []). The journal IS the
// complete record, so fold the WHOLE story back: one Turn per phase entry (its reasoning + the
// facts/nodes it grew from `refs`), the tool-call CARDS from the invocation entries that annotate
// that phase's seq, and the write-gate from the gate_opened entry — so a reopened run reads
// identical to live (reasoning + tool calls + gate), never just bare reasoning.
function obsFromRefs(refs?: JournalEntry["refs"]): TurnObs {
  if (!refs) return emptyObs();
  // DEDUPE (parity with the live fold's mergeIds): a phase's refs list every id it TOUCHED, so a
  // node/fact revisited in the phase appears more than once. Distinct ids keep the counts honest
  // ("discovered X nodes") and the render keys unique.
  const uniq = (xs?: string[]) => [...new Set(xs ?? [])];
  return {
    factIds: uniq(refs.facts),
    nodeIds: uniq(refs.nodes),
    edgeIds: uniq(refs.edges),
    eventIds: uniq(refs.events),
    hypotheses: [], // belief-move detail isn't in refs; the live path builds it from deltas
  };
}

function toolCallFromInvocation(e: JournalEntry, key: number): ToolCall {
  return {
    seq: key, // SYNTHETIC unique key: invocations SHARE their phase's seq, so key by build order
    intent: e.intent ?? "",
    provider: e.provider ?? "",
    effect: e.effect ?? "read",
    op_count: e.op_count ?? 0,
    outcome: e.outcome,
    blocked: e.blocked ?? false,
    reason: e.reason ?? null,
    params: e.params,
    startedAt: e.ts ?? null, // the journal ts is the call's WHEN on a reopen (trace span is ephemeral)
    // kind (tool/workflow) is derived by ToolCallCard from effect when absent; summary was ephemeral
  };
}

function isPhaseEntry(e: JournalEntry): boolean {
  const kind = (e as { kind?: string }).kind;
  // kind==="phase" is the served bundle; kind===undefined tolerates a legacy/hand-authored
  // kind-less phase entry (older snapshots + the test fixtures) with a narrative.
  return kind === "phase" || (kind === undefined && !!e.narrative);
}

function turnsFromJournal(journal: JournalEntry[]): Turn[] {
  const turns: Turn[] = [];
  const bySeq = new Map<number, Turn>(); // phase seq → its turn (annotations share that seq)
  for (const e of journal) {
    if (!isPhaseEntry(e)) continue;
    const turn = newTurn(e.seq, String(e.phase), e.narrative ?? "");
    turn.obs = obsFromRefs(e.refs);
    turns.push(turn);
    bySeq.set(e.seq, turn);
  }
  // attach the tool-call cards: invocation entries annotate their phase's seq
  let callKey = 1;
  for (const e of journal) {
    if ((e as { kind?: string }).kind !== "invocation") continue;
    const turn = bySeq.get(e.seq);
    if (turn) turn.calls.push(toolCallFromInvocation(e, callKey++));
  }
  // attach the write-gate: a gate_opened entry marks its phase's turn. On a SUSPENDED reopen the
  // gated phase never completed (no phase entry), so synthesize its turn from the gate_opened
  // record — exactly as the live path's phase_started(act) creates the act turn.
  for (const e of journal) {
    if ((e as { kind?: string }).kind !== "gate_opened" || !e.gate_id) continue;
    const phase = String(e.phase);
    let turn = [...turns].reverse().find((t) => t.phase === phase && !t.gateId);
    if (!turn) {
      turn = newTurn(e.seq, phase, e.narrative ?? "");
      turns.push(turn);
    }
    turn.gateId = e.gate_id;
  }
  // attach the phase-review: a phase_review entry annotates the COMPLETED phase's turn (the phase
  // ran, THEN paused for the direction review — so its turn already exists, unlike a gate).
  for (const e of journal) {
    if ((e as { kind?: string }).kind !== "phase_review" || !e.review_id) continue;
    const phase = String(e.phase);
    const turn = [...turns].reverse().find((t) => t.phase === phase && !t.reviewId);
    if (turn) turn.reviewId = e.review_id;
  }
  turns.sort((a, b) => a.key - b.key);
  return turns;
}

// ── enrich: thread the JOURNALED per-phase objective + plan + rejections onto the turns ─────
// The live SSE stream carries only phase_started / reasoning / graph_delta / hypotheses_delta —
// the phase GOAL, the planner's PLAN (available/plan_calls/plan_ops), and the reducer rejections
// live ONLY in the journal (served in the bundle). BOTH folds share this ONE enrichment so a live
// cold-load and a disk reopen render the identical turn. It matches each journal phase entry to a
// turn by phase-name occurrence order — robust to turn.key being an EVENT seq live but a JOURNAL
// seq on reopen (the two counters differ) — and pulls the plan (shares the phase's seq) and the
// rejections (rejection.seq === the phase entry's seq). Pure re-derivation: returns NEW turn
// objects for those it enriches, never mutating the prior state's turns.
function enrichTurnsFromJournal(
  turns: Turn[],
  journal: JournalEntry[],
  rejections: RejectionItem[]
): Turn[] {
  // the planner's plan + its reasoning (the objective fallback), keyed by the shared phase seq
  const planBySeq = new Map<number, TurnPlan>();
  const planNarrativeBySeq = new Map<number, string>();
  for (const e of journal) {
    if ((e as { kind?: string }).kind !== "plan") continue;
    planBySeq.set(e.seq, {
      available: e.available ?? [],
      plannedCalls: e.plan_calls ?? [],
      plannedOps: e.plan_ops ?? [],
    });
    if (e.narrative) planNarrativeBySeq.set(e.seq, e.narrative);
  }
  // reducer rejections attributed to a phase entry (rejection.seq === that entry's seq)
  const rejBySeq = new Map<number, RejectionItem[]>();
  for (const r of rejections) {
    const list = rejBySeq.get(r.seq);
    if (list) list.push(r);
    else rejBySeq.set(r.seq, [r]);
  }
  // phase entries queued per phase name, in journal (execution) order — the k-th phase-P entry
  // enriches the k-th phase-P turn (so an investigate LOOP lines up entry ↔ turn without relying
  // on seq equality across the event/journal counters).
  const queues = new Map<string, JournalEntry[]>();
  for (const e of journal) {
    if (!isPhaseEntry(e)) continue;
    const phase = String(e.phase);
    const q = queues.get(phase);
    if (q) q.push(e);
    else queues.set(phase, [e]);
  }
  return turns.map((t) => {
    const entry = queues.get(t.phase)?.shift();
    if (!entry) return t; // no journal phase entry yet (a still-running live phase / synthesized gate turn)
    const plan = planBySeq.get(entry.seq);
    const rej = rejBySeq.get(entry.seq);
    return {
      ...t,
      objective: entry.goal || planNarrativeBySeq.get(entry.seq) || "",
      ...(plan ? { plan } : {}),
      ...(rej && rej.length ? { rejections: rej } : {}),
    };
  });
}

// Reconstruct a live-shaped GateOpenedEvent from a gate_opened journal entry, hydrating the
// serving hypothesis + evidence from the snapshot (the journal stores them by id).
function gateEventFromJournal(
  e: JournalEntry,
  hypById: Map<string, HypothesisItem>,
  factById: Map<string, GraphFact>
): GateOpenedEvent {
  const h = e.hypothesis ? hypById.get(e.hypothesis) : undefined;
  return {
    type: "gate_opened",
    seq: e.seq,
    ts: e.ts ?? "",
    gate_id: e.gate_id ?? "",
    phase: String(e.phase),
    reasoning: e.narrative ?? "",
    actions: e.actions ?? [],
    hypothesis: h
      ? {
          id: h.id,
          statement: h.statement,
          status: h.status,
          confidence: h.confidence,
          root_candidate: h.root_candidate,
        }
      : null,
    evidence: (e.evidence ?? []).map((fid) => {
      const f = factById.get(fid);
      return f
        ? { id: f.id, subject: f.subject, predicate: f.predicate, value: f.value, unit: f.unit, source: f.source, resolved: true }
        : { id: fid, resolved: false };
    }),
  };
}

// Reconstruct a live-shaped PhaseReviewOpenedEvent from a phase_review journal entry, hydrating
// the leading hypothesis by id from the snapshot (the journal stores it by id, like a gate).
function reviewEventFromJournal(
  e: JournalEntry,
  hypById: Map<string, HypothesisItem>
): PhaseReviewOpenedEvent {
  const h = e.hypothesis ? hypById.get(e.hypothesis) : undefined;
  return {
    type: "phase_review_opened",
    seq: e.seq,
    ts: e.ts ?? "",
    review_id: e.review_id ?? "",
    phase: String(e.phase),
    to_phase: e.to_phase ?? "",
    summary: e.narrative ?? "",
    verdict: e.verdict,
    hypothesis: h
      ? { id: h.id, statement: h.statement, status: h.status, confidence: h.confidence, root_candidate: h.root_candidate }
      : null,
    facts: e.facts ?? [],
    nodes: e.nodes ?? [],
  };
}

// Rebuild the gates, phase-reviews, operator decisions and operator messages from the journal on
// reopen, so ApprovalCard / ReviewCard + the two-way chat tail render identically to live.
function hydrateFromJournal(s: LiveState, snap: Snapshot): void {
  const hypById = new Map(snap.hypotheses.map((h) => [h.id, h]));
  const factById = new Map(snap.graph.facts.map((f) => [f.id, f]));
  for (const e of snap.journal) {
    const kind = (e as { kind?: string }).kind;
    if (kind === "gate_opened" && e.gate_id) {
      s.gates[e.gate_id] = gateEventFromJournal(e, hypById, factById);
    } else if (kind === "phase_review" && e.review_id) {
      s.reviews[e.review_id] = reviewEventFromJournal(e, hypById);
    } else if (kind === "gate_decision" || kind === "step") {
      const gateId = (e.action as { gate_id?: string } | undefined)?.gate_id;
      if (gateId && e.decision) {
        s.decisions[gateId] = {
          decision: e.decision as GateDecision,
          reason: e.narrative || undefined,
          actor: e.actor,
          source: e.source ?? undefined,
        };
      }
    } else if (kind === "review_decision") {
      const rid = e.review_id ?? (e.action as { review_id?: string } | undefined)?.review_id;
      if (rid && e.decision) {
        s.reviewDecisions[rid] = {
          decision: e.decision as GateDecision,
          reason: e.narrative || undefined,
          actor: e.actor,
          source: e.source ?? undefined,
        };
      }
    } else if (kind === "message") {
      const msgKind = (e.action as { kind?: string } | undefined)?.kind ?? "steer";
      s.messages.push({
        seq: e.seq,
        text: e.narrative ?? "",
        kind: msgKind,
        actor: e.actor,
        phase: e.phase != null ? String(e.phase) : null,
      });
    }
  }
  // the currently-open gate / review on a suspended reopen (for the live card state)
  if (snap.state === "suspended" && snap.pending_gate) s.gate = snap.pending_gate;
  if (snap.state === "awaiting_review" && snap.pending_review) s.review = snap.pending_review;
}

// ── seed: full cold-load from the snapshot bundle, then replay its events ───────────
function seed(snap: Snapshot): LiveState {
  const s = emptyState();
  s.sessionId = snap.session_id;
  s.subject = snap.subject;
  s.state = snap.state;
  s.outcome = snap.outcome;
  for (const n of snap.graph.nodes)
    s.nodes[n.id] = {
      id: n.id,
      type: n.type,
      props: n.props,
      created_by: 0,
      origin: n.origin,
      source: n.source,
      first_source: n.first_source,
      first_seen: n.first_seen,
      last_seen: n.last_seen,
    };
  for (const e of snap.graph.edges) s.edges[e.id] = e;
  for (const f of snap.graph.facts) s.facts[f.id] = f;
  for (const ev of snap.graph.events) s.events[ev.id] = ev;
  for (const h of snap.hypotheses) s.hypotheses[h.id] = h;
  s.hypothesisOrder = snap.hypotheses.map((h) => h.id); // the ENGINE's ranked() order
  s.discovery = snap.discovery ?? s.discovery;
  s.rejections = snap.rejections ?? s.rejections;
  // replay the recorded event stream to build the chat, node badges, phase + open gate
  const grown = applyEvents(s, snap.events, /*fresh*/ true);
  // Reopen (no live stream): the durable JOURNAL is the complete record — fold the WHOLE story
  // back so a reopened investigation reads like it happened: reasoning + tool-call cards + the
  // write-gate + the operator tail, per phase (the stepper + the ×N iteration badges seed from
  // these turns via phaseCounts). Live runs (events present) are untouched.
  if (snap.events.length === 0 && snap.journal.length > 0) {
    grown.turns = turnsFromJournal(snap.journal);
    for (const t of grown.turns)
      if (!grown.phasesRun.includes(t.phase)) grown.phasesRun.push(t.phase);
    hydrateFromJournal(grown, snap); // gates + decisions + operator messages from the journal
  }
  // thread the journaled per-phase objective + plan + rejections onto the turns (BOTH folds) — a
  // live cold-load and a disk reopen end at the identical enriched shape.
  grown.turns = enrichTurnsFromJournal(grown.turns, snap.journal, grown.rejections);
  return grown;
}

// ── merge: refresh full node props / hypotheses statements / facts after a live grow ────
function mergeDetail(state: LiveState, snap: Snapshot): LiveState {
  const nodes = { ...state.nodes };
  for (const n of snap.graph.nodes) {
    const existing = nodes[n.id];
    nodes[n.id] = {
      id: n.id,
      type: n.type,
      props: n.props,
      created_by: existing?.created_by ?? 0,
      origin: n.origin ?? existing?.origin,
      source: n.source ?? existing?.source,
      first_source: n.first_source ?? existing?.first_source,
      first_seen: n.first_seen ?? existing?.first_seen,
      last_seen: n.last_seen ?? existing?.last_seen,
    };
  }
  const edges = { ...state.edges };
  for (const e of snap.graph.edges) edges[e.id] = e;
  const facts = { ...state.facts };
  for (const f of snap.graph.facts) facts[f.id] = f;
  const events = { ...state.events };
  for (const ev of snap.graph.events) events[ev.id] = ev;
  const hypotheses = { ...state.hypotheses };
  for (const h of snap.hypotheses) hypotheses[h.id] = h;
  // refresh the ENGINE ranked() order; keep any delta-born ids the snapshot hasn't caught up on
  const ranked = snap.hypotheses.map((h) => h.id);
  const hypothesisOrder = mergeIds(ranked, state.hypothesisOrder);
  const rejections = snap.rejections ?? state.rejections;
  return {
    ...state,
    nodes,
    edges,
    facts,
    events,
    hypotheses,
    hypothesisOrder,
    discovery: snap.discovery ?? state.discovery,
    rejections,
    // re-thread the journaled objective/plan/rejections onto the live turns (the SSE stream never
    // carried them; reconcile's fresh bundle does) — keeps live parity with the reopen fold.
    turns: enrichTurnsFromJournal(state.turns, snap.journal, rejections),
    outcome: snap.outcome,
  };
}

// ── the event fold ──────────────────────────────────────────────────────────────
function applyEvents(prev: LiveState, evs: SessionEvent[], fresh = false): LiveState {
  const s: LiveState = {
    ...prev,
    nodes: { ...prev.nodes },
    edges: { ...prev.edges },
    facts: { ...prev.facts },
    events: { ...prev.events },
    hypotheses: { ...prev.hypotheses },
    turns: [...prev.turns],
    messages: [...prev.messages],
    gates: { ...prev.gates },
    decisions: { ...prev.decisions },
    reviews: { ...prev.reviews },
    reviewDecisions: { ...prev.reviewDecisions },
    phasesRun: [...prev.phasesRun],
  };
  for (const ev of evs) {
    if (!fresh && ev.seq <= prev.lastSeq) continue; // idempotent: never re-apply a seq
    applyOne(s, ev);
    if (ev.seq > s.lastSeq) s.lastSeq = ev.seq;
  }
  return s;
}

function mergeIds(existing: string[], incoming: string[]): string[] {
  if (incoming.length === 0) return existing;
  const seen = new Set(existing);
  const out = [...existing];
  for (const id of incoming) {
    if (!seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

function currentTurn(s: LiveState): Turn | undefined {
  return s.turns[s.turns.length - 1];
}

function mutateTurn(s: LiveState, fn: (t: Turn) => Turn): void {
  const i = s.turns.length - 1;
  if (i < 0) return;
  s.turns[i] = fn({ ...s.turns[i] });
}

function applyOne(s: LiveState, ev: SessionEvent): void {
  switch (ev.type) {
    case "phase_started": {
      s.turns.push(newTurn(ev.seq, ev.phase));
      if (!s.phasesRun.includes(ev.phase)) s.phasesRun.push(ev.phase);
      break;
    }
    case "reasoning": {
      mutateTurn(s, (t) => ({ ...t, reasoning: ev.narrative }));
      break;
    }
    case "capability_call": {
      const call: ToolCall = {
        seq: ev.seq,
        intent: ev.intent,
        provider: ev.provider,
        effect: ev.effect,
        op_count: ev.op_count,
        outcome: ev.outcome,
        blocked: ev.blocked,
        reason: ev.reason,
        kind: ev.kind,
        startedAt: ev.started_at,
        durationMs: ev.duration_ms,
        params: ev.params,
        summary: ev.summary,
      };
      mutateTurn(s, (t) => ({ ...t, calls: [...t.calls, call] }));
      break;
    }
    case "graph_delta": {
      for (const n of ev.nodes) {
        const existing = s.nodes[n.id];
        s.nodes[n.id] = {
          id: n.id,
          type: n.type,
          props: existing?.props ?? {},
          created_by: n.created_by,
          origin: n.origin ?? existing?.origin,
          source: existing?.source,
          first_source: existing?.first_source,
          first_seen: existing?.first_seen,
          last_seen: existing?.last_seen,
        };
      }
      for (const e of ev.edges) {
        if (!s.edges[e.id]) {
          s.edges[e.id] = {
            ...e,
            confidence: null,
            source: e.source ?? null,
            established: e.established ?? null,
          };
        }
      }
      for (const f of ev.facts) {
        if (!s.facts[f.id]) {
          s.facts[f.id] = {
            id: f.id,
            subject: f.subject,
            predicate: f.predicate,
            value: f.value,
            unit: f.unit ?? null,
            where: f.where ?? null,
            at: f.at ?? ev.ts,
            observed_at: f.observed_at ?? null,
            valid_to: null,
            source: f.source ?? "", // obs 7: WHO — no longer blanked on the live stream
            state: "active",
            ...(f.provisional ? { provisional: true } : {}),
          };
        }
      }
      for (const gEv of ev.events) {
        if (!s.events[gEv.id]) {
          s.events[gEv.id] = {
            id: gEv.id,
            entity: gEv.entity,
            type: gEv.type,
            at: ev.ts,
            payload: {},
            source: "",
            ...(gEv.provisional ? { provisional: true } : {}),
          };
        }
      }
      // record what this phase observed (facts gathered, nodes/edges/events discovered)
      mutateTurn(s, (t) => ({
        ...t,
        obs: {
          ...t.obs,
          nodeIds: mergeIds(t.obs.nodeIds, ev.nodes.map((n) => n.id)),
          factIds: mergeIds(t.obs.factIds, ev.facts.map((f) => f.id)),
          edgeIds: mergeIds(t.obs.edgeIds, ev.edges.map((e) => e.id)),
          eventIds: mergeIds(t.obs.eventIds, ev.events.map((e) => e.id)),
        },
      }));
      break;
    }
    case "hypotheses_delta": {
      // GHOST-CARD GUARD (audit §1.3): a delta can reference an id the engine's store never
      // held (a silently-dropped update to an unknown hypothesis) — it arrives as a bare id
      // with no statement and null status/confidence. Fabricating a "proposed / 0%" card from
      // it would show a hypothesis the engine does not hold; skip it entirely.
      const real = ev.hypotheses.filter((h) => Boolean(s.hypotheses[h.id]) || Boolean(h.statement));
      for (const h of real) {
        const existing = s.hypotheses[h.id];
        s.hypotheses[h.id] = {
          id: h.id,
          statement: h.statement || existing?.statement || h.id,
          status: h.status ?? existing?.status ?? "proposed",
          confidence: h.confidence ?? existing?.confidence ?? 0,
          basis: h.basis || existing?.basis || "",
          root_candidate: h.root_candidate ?? existing?.root_candidate ?? null,
          supporting: h.supporting ?? existing?.supporting ?? [],
          refuting: h.refuting ?? existing?.refuting ?? [],
          chain: existing?.chain ?? [],
        };
      }
      s.hypothesisOrder = mergeIds(s.hypothesisOrder, real.map((h) => h.id));
      // the belief movements this phase produced (proposed / supported / refuted)
      const moves: HypothesisMove[] = real.map((h) => ({
        id: h.id,
        action: h.action,
        status: h.status,
        basis: h.basis,
      }));
      mutateTurn(s, (t) => ({ ...t, obs: { ...t.obs, hypotheses: [...t.obs.hypotheses, ...moves] } }));
      break;
    }
    case "gate_opened": {
      s.gate = ev;
      s.gates[ev.gate_id] = ev;
      mutateTurn(s, (t) => ({ ...t, gateId: ev.gate_id }));
      break;
    }
    case "gate_decision": {
      // WHO approved/denied the write-gate — enrich the (optimistic) decision with the engine's
      // recorded actor + Source.HUMAN so the journal + chat show the human in the loop.
      const prev = s.decisions[ev.gate_id];
      s.decisions[ev.gate_id] = {
        decision: (ev.decision as GateDecision) ?? prev?.decision ?? "approve",
        reason: ev.reason || prev?.reason,
        actor: ev.actor,
        source: ev.source,
      };
      break;
    }
    case "phase_review_opened": {
      // the between-phases DIRECTION review — parallels gate_opened: set the currently-open review,
      // index it by review_id, and stamp the current turn (the completed phase) with its review_id.
      s.review = ev;
      s.reviews[ev.review_id] = ev;
      mutateTurn(s, (t) => ({ ...t, reviewId: ev.review_id }));
      break;
    }
    case "phase_review_decision": {
      // WHO approved/refined/denied the advance — enrich the optimistic decision with the actor.
      const prevR = s.reviewDecisions[ev.review_id];
      s.reviewDecisions[ev.review_id] = {
        decision: (ev.decision as GateDecision) ?? prevR?.decision ?? "approve",
        reason: ev.reason || prevR?.reason,
        actor: ev.actor,
        source: ev.source,
      };
      break;
    }
    case "user_message": {
      // an operator turn in the two-way chat (obs 2) — interleaved with phase turns by seq
      s.messages.push({ seq: ev.seq, text: ev.text, kind: ev.kind, actor: ev.actor, phase: ev.phase });
      break;
    }
    case "session_error": {
      s.error = ev.message;
      break;
    }
    case "session_state": {
      s.state = ev.state;
      if (ev.outcome) s.outcome = ev.outcome;
      if (ev.state !== "suspended") s.gate = null;
      // the open review clears the moment the run leaves the review pause (advance/refine/deny/close)
      if (ev.state !== "awaiting_review") s.review = null;
      break;
    }
  }
}

// ── selectors the panes use ────────────────────────────────────────────────────────

/** Graph nodes in creation order (the badge number). */
export function nodesInOrder(s: LiveState): LiveNode[] {
  return Object.values(s.nodes).sort((a, b) => a.created_by - b.created_by || a.id.localeCompare(b.id));
}

/** A node carrying its DENSE creation-order badge (1, 2, 3, …) — not the raw `created_by`
 *  journal seq (which is a PHASE seq: every node born in the same phase shares it). For a
 *  ServiceNow major incident the ORIGIN (the incident record) sorts to #1 (obs 1); the
 *  symptom anomaly and everything else follow in creation order. */
export interface OrderedNode extends LiveNode {
  order: number;
}

function originRank(n: LiveNode): number {
  if (n.origin) return 0; // the ServiceNow incident under investigation → #1
  if (n.type === "anomaly") return 1; // the symptom, next
  return 2;
}

export function nodesWithOrder(s: LiveState): OrderedNode[] {
  const sorted = Object.values(s.nodes).sort(
    (a, b) =>
      originRank(a) - originRank(b) ||
      a.created_by - b.created_by ||
      a.id.localeCompare(b.id)
  );
  return sorted.map((n, i) => ({ ...n, order: i + 1 }));
}

const RELATED_EDGE_TYPES = new Set(["similar_to", "recurrence_of"]);

export interface RelatedIncident {
  node: LiveNode;
  relation: string; // similar_to | recurrence_of
  confidence: number | null;
}

/** The SIMILAR_TO / RECURRENCE_OF-linked prior incidents — "N other apps reported the same
 *  symptom in the same window" — surfaced as a hypothesis prior (UI-SPEC / DEPTH pass). */
export function relatedIncidents(s: LiveState): RelatedIncident[] {
  const primaryId = `incident:${(s.subject?.id ?? "").toLowerCase()}`;
  const edgeFor = new Map<string, GraphEdge>();
  for (const e of Object.values(s.edges)) {
    if (!RELATED_EDGE_TYPES.has(e.type)) continue;
    const other = e.src === primaryId ? e.dst : e.src;
    if (!edgeFor.has(other)) edgeFor.set(other, e);
  }
  return Object.values(s.nodes)
    .filter((n) => n.type === "incident" && n.id !== primaryId)
    .map((n) => {
      const e = edgeFor.get(n.id);
      return { node: n, relation: e?.type ?? "similar_to", confidence: e?.confidence ?? null };
    })
    .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0) || a.node.id.localeCompare(b.node.id));
}

/** Hypotheses in the ENGINE's ranked() order (the bundle order, delta-born ids appended) —
 *  the single ranking authority. No client-side re-sort (the audit's divergent-resort fix). */
export function hypothesisList(s: LiveState): HypothesisItem[] {
  const out: HypothesisItem[] = [];
  const seen = new Set<string>();
  for (const id of s.hypothesisOrder) {
    const h = s.hypotheses[id];
    if (h && !seen.has(id)) {
      seen.add(id);
      out.push(h);
    }
  }
  for (const h of Object.values(s.hypotheses)) {
    if (!seen.has(h.id)) out.push(h); // defensive: never drop a held hypothesis
  }
  return out;
}

/** The phase whose turn is currently on screen (for the stepper highlight). */
export function activePhase(s: LiveState): string | null {
  return currentTurn(s)?.phase ?? null;
}

/** How many turns each phase has run. INVESTIGATE is ONE loop (P7): a run's phase sequence
 *  repeats it (frame, investigate, investigate, act, …) — the stepper collapses repeats onto
 *  the single investigate step and shows this count as an ×N iteration badge. */
export function phaseCounts(s: LiveState): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const t of s.turns) counts[t.phase] = (counts[t.phase] ?? 0) + 1;
  return counts;
}
