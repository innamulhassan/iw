// The live investigation store — a reducer that mirrors the engine's ordered event stream
// (phase_started · reasoning · capability_call · graph_delta · ledger_delta · gate_opened ·
// session_state) into the shape the panes render. The engine is the single source of truth:
// nothing here is invented, every node/fact/hypothesis is upserted from a delta the engine
// emitted. Cold-load seeds full detail from the snapshot bundle; SSE deltas then grow it live.
import type {
  GateOpenedEvent,
  GraphEdge,
  GraphEvent,
  GraphFact,
  LedgerItem,
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

/** A cross-pane selection (obs 8): clicking a fact in the ledger highlights that node + fact in
 *  the graph, and vice-versa. `id` is a node id or fact id per `kind`. */
export interface Selection {
  kind: "node" | "fact";
  id: string;
}

/** One ledger movement observed during a phase (a hypothesis proposed / supported / refuted). */
export interface LedgerMove {
  id: string;
  action: string;
  status: string | null;
  basis: string;
}

/** What a phase OBSERVED — the graph it grew (facts gathered, nodes/edges/events discovered)
 *  and the beliefs it moved. Accumulated from the same graph_delta / ledger_delta the engine
 *  emitted, so the journal can show the full per-phase sequence, not just the summary. */
export interface TurnObs {
  factIds: string[];
  nodeIds: string[];
  edgeIds: string[];
  eventIds: string[];
  ledger: LedgerMove[];
}

function emptyObs(): TurnObs {
  return { factIds: [], nodeIds: [], edgeIds: [], eventIds: [], ledger: [] };
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
  ledger: Record<string, LedgerItem>;
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
    ledger: {},
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
  for (const h of snap.ledger) s.ledger[h.id] = h;
  // replay the recorded event stream to build the chat, node badges, phase + open gate
  const grown = applyEvents(s, snap.events, /*fresh*/ true);
  return grown;
}

// ── merge: refresh full node props / ledger statements / facts after a live grow ────
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
  const ledger = { ...state.ledger };
  for (const h of snap.ledger) ledger[h.id] = h;
  return { ...state, nodes, edges, facts, events, ledger, outcome: snap.outcome };
}

// ── the event fold ──────────────────────────────────────────────────────────────
function applyEvents(prev: LiveState, evs: SessionEvent[], fresh = false): LiveState {
  const s: LiveState = {
    ...prev,
    nodes: { ...prev.nodes },
    edges: { ...prev.edges },
    facts: { ...prev.facts },
    events: { ...prev.events },
    ledger: { ...prev.ledger },
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
    case "ledger_delta": {
      for (const h of ev.hypotheses) {
        const existing = s.ledger[h.id];
        s.ledger[h.id] = {
          id: h.id,
          statement: existing?.statement ?? h.id,
          status: h.status ?? existing?.status ?? "proposed",
          confidence: h.confidence ?? existing?.confidence ?? 0,
          basis: h.basis || existing?.basis || "",
          root_candidate: existing?.root_candidate ?? null,
          supporting: existing?.supporting ?? [],
          refuting: existing?.refuting ?? [],
          chain: existing?.chain ?? [],
        };
      }
      // the belief movements this phase produced (proposed / supported / refuted)
      const moves: LedgerMove[] = ev.hypotheses.map((h) => ({
        id: h.id,
        action: h.action,
        status: h.status,
        basis: h.basis,
      }));
      mutateTurn(s, (t) => ({ ...t, obs: { ...t.obs, ledger: [...t.obs.ledger, ...moves] } }));
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

export function ledgerList(s: LiveState): LedgerItem[] {
  return Object.values(s.ledger);
}

/** The phase whose turn is currently on screen (for the stepper highlight). */
export function activePhase(s: LiveState): string | null {
  return currentTurn(s)?.phase ?? null;
}
