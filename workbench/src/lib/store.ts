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

/** One chat turn = the agent's work in one phase: its reasoning + the tool calls it made +
 *  what it observed (+ the write-gate, when this phase opened one). */
export interface Turn {
  key: number; // the phase_started seq — stable react key
  phase: string;
  reasoning: string;
  calls: ToolCall[];
  obs: TurnObs;
  gateId?: string;
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
  | { kind: "decision"; gateId: string; decision: GateDecision; reason?: string };

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
  return {
    factIds: refs.facts ?? [],
    nodeIds: refs.nodes ?? [],
    edgeIds: refs.edges ?? [],
    eventIds: refs.events ?? [],
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
    const turn: Turn = {
      key: e.seq,
      phase: String(e.phase),
      reasoning: e.narrative ?? "",
      calls: [],
      obs: obsFromRefs(e.refs),
    };
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
      turn = { key: e.seq, phase, reasoning: e.narrative ?? "", calls: [], obs: emptyObs() };
      turns.push(turn);
    }
    turn.gateId = e.gate_id;
  }
  turns.sort((a, b) => a.key - b.key);
  return turns;
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

// Rebuild the gates, operator decisions and operator messages from the journal on reopen, so
// ApprovalCard + the two-way chat tail render identically to live (the journal is the record).
function hydrateFromJournal(s: LiveState, snap: Snapshot): void {
  const hypById = new Map(snap.hypotheses.map((h) => [h.id, h]));
  const factById = new Map(snap.graph.facts.map((f) => [f.id, f]));
  for (const e of snap.journal) {
    const kind = (e as { kind?: string }).kind;
    if (kind === "gate_opened" && e.gate_id) {
      s.gates[e.gate_id] = gateEventFromJournal(e, hypById, factById);
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
  // the currently-open gate on a suspended reopen (for ApprovalCard's live state)
  if (snap.state === "suspended" && snap.pending_gate) s.gate = snap.pending_gate;
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
  return {
    ...state,
    nodes,
    edges,
    facts,
    events,
    hypotheses,
    hypothesisOrder,
    discovery: snap.discovery ?? state.discovery,
    rejections: snap.rejections ?? state.rejections,
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
      s.turns.push({ key: ev.seq, phase: ev.phase, reasoning: "", calls: [], obs: emptyObs() });
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
