import { describe, expect, it } from "vitest";
import type { SessionEvent, Snapshot } from "../types";
import {
  activePhase,
  emptyState,
  hypothesisList,
  nodesInOrder,
  nodesWithOrder,
  phaseCounts,
  reduce,
  relatedIncidents,
} from "./store";

const events: SessionEvent[] = [
  { seq: 1, ts: "t", type: "phase_started", phase: "frame" },
  { seq: 2, ts: "t", type: "reasoning", phase: "frame", narrative: "5xx spiked after deploy" },
  {
    seq: 3,
    ts: "t",
    type: "capability_call",
    intent: "active_alerts",
    provider: "prometheus",
    effect: "read",
    op_count: 4,
    blocked: false,
    reason: null,
  },
  {
    seq: 4,
    ts: "t",
    type: "graph_delta",
    nodes: [
      { id: "service:pay", type: "service", created_by: 1 },
      { id: "anomaly:a1", type: "anomaly", created_by: 1 },
    ],
    edges: [],
    facts: [],
    events: [],
  },
  { seq: 5, ts: "t", type: "session_state", state: "running", phase: "investigate" },
];

describe("store reducer — the live event fold", () => {
  it("builds a chat turn per phase with reasoning + tool-call cards", () => {
    const s = reduce(emptyState(), { kind: "events", events });
    expect(s.turns).toHaveLength(1);
    expect(s.turns[0].phase).toBe("frame");
    expect(s.turns[0].reasoning).toMatch(/5xx spiked/);
    expect(s.turns[0].calls).toHaveLength(1);
    expect(s.turns[0].calls[0].intent).toBe("active_alerts");
    expect(activePhase(s)).toBe("frame");
    expect(s.lastSeq).toBe(5);
  });

  it("materialises graph nodes carrying their created_by badge, in creation order", () => {
    const s = reduce(emptyState(), { kind: "events", events });
    const ordered = nodesInOrder(s);
    expect(ordered).toHaveLength(2);
    expect(ordered.every((n) => n.created_by === 1)).toBe(true);
    expect(s.nodes["anomaly:a1"].type).toBe("anomaly");
  });

  it("is idempotent — a re-delivered seq is never applied twice", () => {
    const once = reduce(emptyState(), { kind: "events", events });
    const twice = reduce(once, { kind: "events", events });
    expect(twice.turns).toHaveLength(1);
    expect(nodesInOrder(twice)).toHaveLength(2);
    expect(twice.turns[0].calls).toHaveLength(1);
  });

  it("collapses the repeated investigate loop: phasesRun stays unique, phaseCounts carries ×N", () => {
    // P7: investigate is ONE loop — the engine emits it repeatedly (frame, investigate,
    // investigate, act, …); the stepper must show ONE investigate step with an iteration count.
    const evs: SessionEvent[] = [
      { seq: 1, ts: "t", type: "phase_started", phase: "frame" },
      { seq: 2, ts: "t", type: "phase_started", phase: "investigate" },
      { seq: 3, ts: "t", type: "phase_started", phase: "investigate" },
      { seq: 4, ts: "t", type: "phase_started", phase: "act" },
    ];
    const s = reduce(emptyState(), { kind: "events", events: evs });
    expect(s.phasesRun).toEqual(["frame", "investigate", "act"]); // unique, in order
    expect(s.turns).toHaveLength(4); // but every loop turn is kept for the chat/journal
    expect(phaseCounts(s)).toEqual({ frame: 1, investigate: 2, act: 1 });
    expect(activePhase(s)).toBe("act");
  });

  it("opens a write-gate and records the operator decision", () => {
    let s = reduce(emptyState(), { kind: "events", events });
    s = reduce(s, {
      kind: "events",
      events: [
        { seq: 6, ts: "t", type: "phase_started", phase: "act" },
        {
          seq: 7,
          ts: "t",
          type: "gate_opened",
          gate_id: "g1",
          phase: "act",
          reasoning: "roll back",
          actions: [{ intent: "apply_remediation", params: {}, provider: "remediation", effect: "write", summary: "rollback" }],
          hypothesis: { id: "hyp:h1", statement: "deploy broke it", status: "supported", confidence: 0.9, root_candidate: "code_commit:abc" },
          evidence: [],
        },
        { seq: 8, ts: "t", type: "session_state", state: "suspended", phase: "act" },
      ],
    });
    expect(s.state).toBe("suspended");
    expect(s.gate?.gate_id).toBe("g1");
    expect(s.turns.at(-1)?.gateId).toBe("g1");
    expect(s.gates["g1"].hypothesis?.statement).toBe("deploy broke it");

    const decided = reduce(s, { kind: "decision", gateId: "g1", decision: "approve" });
    expect(decided.gate).toBeNull();
    expect(decided.decisions["g1"].decision).toBe("approve");
  });

  it("badges nodes with a DENSE creation order (symptom #1) — not the raw phase seq", () => {
    // two nodes born in phase 1 (created_by 1) + one in phase 2 (created_by 2): the badges must
    // be a dense 1,2,3 in creation order, with the anomaly (symptom) sorted first — NOT 1,1,2.
    const evs: SessionEvent[] = [
      { seq: 1, ts: "t", type: "phase_started", phase: "frame" },
      {
        seq: 2,
        ts: "t",
        type: "graph_delta",
        nodes: [
          { id: "service:pay", type: "service", created_by: 1 },
          { id: "anomaly:a1", type: "anomaly", created_by: 1 },
        ],
        edges: [],
        facts: [{ id: "f1", subject: "service:pay", predicate: "red_errors", value: 0.4 }],
        events: [],
      },
      { seq: 3, ts: "t", type: "phase_started", phase: "investigate" },
      {
        seq: 4,
        ts: "t",
        type: "graph_delta",
        nodes: [{ id: "incident:inc-1", type: "incident", created_by: 2 }],
        edges: [],
        facts: [],
        events: [],
      },
    ];
    const s = reduce(emptyState(), { kind: "events", events: evs });
    const ordered = nodesWithOrder(s);
    expect(ordered.map((n) => n.order)).toEqual([1, 2, 3]);
    expect(new Set(ordered.map((n) => n.order)).size).toBe(3); // dense + unique
    expect(ordered[0].type).toBe("anomaly"); // the entry-point symptom is #1
    // and the per-phase observations captured the fact the frame phase gathered
    const frame = s.turns.find((t) => t.phase === "frame");
    expect(frame?.obs.factIds).toContain("f1");
    expect(frame?.obs.nodeIds).toHaveLength(2);
  });

  it("surfaces SIMILAR_TO-linked related incidents, and records WHO approved the gate", () => {
    const snapshot = {
      session_id: "app-incident:INC-1",
      subject: { domain: "app-incident", id: "INC-1", kind: "incident" },
      state: "running",
      outcome: "open",
      phases: ["frame"],
      graph: { nodes: [], edges: [], facts: [], events: [] },
      hypotheses: [],
      journal: [],
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: null,
      messages: [],
      events: [
        { seq: 1, ts: "t", type: "phase_started", phase: "investigate" },
        {
          seq: 2,
          ts: "t",
          type: "graph_delta",
          nodes: [
            { id: "incident:inc-1", type: "incident", created_by: 1 },
            { id: "incident:inc-2", type: "incident", created_by: 1 },
          ],
          edges: [{ id: "e1", type: "similar_to", src: "incident:inc-1", dst: "incident:inc-2", origin: "inferred" }],
          facts: [],
          events: [],
        },
      ],
    } as unknown as Snapshot;

    const s = reduce(emptyState(), { kind: "seed", snapshot });
    const related = relatedIncidents(s);
    expect(related).toHaveLength(1);
    expect(related[0].node.id).toBe("incident:inc-2"); // the prior, not the primary
    expect(related[0].relation).toBe("similar_to");

    const decided = reduce(s, {
      kind: "events",
      events: [
        { seq: 3, ts: "t", type: "phase_started", phase: "act" },
        { seq: 4, ts: "t", type: "gate_opened", gate_id: "g1", phase: "act", reasoning: "roll back", actions: [], hypothesis: null, evidence: [] },
        { seq: 5, ts: "t", type: "gate_decision", gate_id: "g1", decision: "approve", actor: "alice@oncall", source: "human", reason: "", phase: "act" },
      ],
    });
    expect(decided.decisions["g1"].decision).toBe("approve");
    expect(decided.decisions["g1"].actor).toBe("alice@oncall");
    expect(decided.decisions["g1"].source).toBe("human");
  });

  it("carries the invocation OUTCOME on the tool call — error is a failed call, never 'no data'", () => {
    const evs: SessionEvent[] = [
      { seq: 1, ts: "t", type: "phase_started", phase: "investigate" },
      {
        seq: 2,
        ts: "t",
        type: "capability_call",
        intent: "fetch_metrics",
        provider: "prometheus",
        effect: "read",
        op_count: 0,
        outcome: "error",
        blocked: false,
        reason: "HTTP 503 from provider",
      },
      {
        seq: 3,
        ts: "t",
        type: "capability_call",
        intent: "search_fw_denies",
        provider: "firewall",
        effect: "read",
        op_count: 0,
        outcome: "empty",
        blocked: false,
        reason: null,
      },
    ];
    const s = reduce(emptyState(), { kind: "events", events: evs });
    expect(s.turns[0].calls[0].outcome).toBe("error");
    expect(s.turns[0].calls[1].outcome).toBe("empty");
  });

  it("never fabricates a ghost card from a bare hypothesis id the engine does not hold", () => {
    const evs: SessionEvent[] = [
      { seq: 1, ts: "t", type: "phase_started", phase: "investigate" },
      {
        seq: 2,
        ts: "t",
        type: "hypotheses_delta",
        hypotheses: [
          // a silently-dropped update to an unknown id: bare id, no statement, null status
          { id: "hyp:phantom", action: "attach_evidence", status: null, confidence: null, basis: "" },
          // a REAL proposal carries its statement on the delta
          {
            id: "hyp:h1",
            action: "create",
            status: "proposed",
            confidence: 0.4,
            basis: "deploy window matches",
            statement: "the deploy broke it",
          },
        ],
      },
    ];
    const s = reduce(emptyState(), { kind: "events", events: evs });
    expect(s.hypotheses["hyp:phantom"]).toBeUndefined();
    expect(hypothesisList(s).map((h) => h.id)).toEqual(["hyp:h1"]);
    // and the phantom never shows up as a belief move either
    expect(s.turns[0].obs.hypotheses.map((m) => m.id)).toEqual(["hyp:h1"]);
  });

  it("renders hypotheses in the ENGINE ranked() order — no client-side re-sort", () => {
    // engine order deliberately NOT confidence-descending: the engine ranks by status first
    const ranked = [
      { id: "hyp:low", statement: "low-score leader", status: "supported", confidence: 0.35, basis: "b", root_candidate: null, supporting: [], refuting: [], chain: [] },
      { id: "hyp:high", statement: "high-score rival", status: "proposed", confidence: 0.9, basis: "b", root_candidate: null, supporting: [], refuting: [], chain: [] },
    ];
    const snapshot = {
      session_id: "app-incident:INC-1",
      subject: { domain: "app-incident", id: "INC-1", kind: "incident" },
      state: "running",
      outcome: "open",
      phases: ["frame"],
      graph: { nodes: [], edges: [], facts: [], events: [] },
      hypotheses: ranked,
      journal: [],
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: null,
      messages: [],
      events: [],
    } as unknown as Snapshot;
    let s = reduce(emptyState(), { kind: "seed", snapshot });
    expect(hypothesisList(s).map((h) => h.id)).toEqual(["hyp:low", "hyp:high"]);

    // a delta-born hypothesis appends until the next snapshot merge re-ranks it
    s = reduce(s, {
      kind: "events",
      events: [
        { seq: 1, ts: "t", type: "phase_started", phase: "investigate" },
        {
          seq: 2,
          ts: "t",
          type: "hypotheses_delta",
          hypotheses: [
            { id: "hyp:new", action: "create", status: "proposed", confidence: 0.99, basis: "b", statement: "brand new" },
          ],
        },
      ],
    });
    expect(hypothesisList(s).map((h) => h.id)).toEqual(["hyp:low", "hyp:high", "hyp:new"]);

    // mergeDetail refreshes to the engine's NEW ranked order
    const reRanked = { ...snapshot, hypotheses: [ranked[1], ranked[0]] } as unknown as Snapshot;
    s = reduce(s, { kind: "mergeDetail", snapshot: reRanked });
    expect(hypothesisList(s).map((h) => h.id)).toEqual(["hyp:high", "hyp:low", "hyp:new"]);
  });

  it("seeds discovery counters + rejections from the bundle and marks provisional assertions", () => {
    const snapshot = {
      session_id: "app-incident:INC-1",
      subject: { domain: "app-incident", id: "INC-1", kind: "incident" },
      state: "running",
      outcome: "open",
      phases: ["frame"],
      graph: {
        nodes: [],
        edges: [],
        facts: [
          { id: "f1", subject: "service:pay", predicate: "x.appd.weird_metric", value: 1, unit: null, at: "t", valid_to: null, source: "appdynamics", state: "active", provisional: true },
        ],
        events: [],
      },
      hypotheses: [],
      journal: [],
      rejections: [{ seq: 4, phase: "investigate", op_index: 2, op_kind: "AddFact", reason: "unknown predicate" }],
      discovery: { class_hints: { LoadBalancer: 3 }, quarantined_names: { "x.appd.weird_metric": 2 } },
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: null,
      messages: [],
      events: [],
    } as unknown as Snapshot;
    const s = reduce(emptyState(), { kind: "seed", snapshot });
    expect(s.discovery.class_hints).toEqual({ LoadBalancer: 3 });
    expect(s.discovery.quarantined_names).toEqual({ "x.appd.weird_metric": 2 });
    expect(s.rejections).toHaveLength(1);
    expect(s.rejections[0].reason).toBe("unknown predicate");
    expect(s.facts["f1"].provisional).toBe(true);

    // and a provisional fact arriving on the LIVE stream keeps its flag too
    const grown = reduce(s, {
      kind: "events",
      events: [
        { seq: 1, ts: "t", type: "phase_started", phase: "investigate" },
        {
          seq: 2,
          ts: "t",
          type: "graph_delta",
          nodes: [],
          edges: [],
          facts: [{ id: "f2", subject: "service:pay", predicate: "x.appd.other", value: 2, provisional: true }],
          events: [{ id: "ev1", entity: "service:pay", type: "x.appd.blip", provisional: true }],
        },
      ],
    });
    expect(grown.facts["f2"].provisional).toBe(true);
    expect(grown.events["ev1"].provisional).toBe(true);
  });

  it("seeds full detail from a snapshot then replays its events for badges + chat", () => {
    const snapshot = {
      session_id: "app-incident:INC-1",
      subject: { domain: "app-incident", id: "INC-1", kind: "incident" },
      state: "suspended",
      outcome: "open",
      phases: ["frame"],
      graph: { nodes: [{ id: "service:pay", type: "service", props: { service_name: "pay" } }], edges: [], facts: [], events: [] },
      hypotheses: [{ id: "hyp:h1", statement: "deploy broke it", status: "supported", confidence: 0.9, basis: "b", root_candidate: null, supporting: [], refuting: [], chain: [] }],
      journal: [],
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: null,
      messages: [],
      events,
    } as unknown as Snapshot;

    const s = reduce(emptyState(), { kind: "seed", snapshot });
    // props from the bundle, created_by from the replayed event stream
    expect(s.nodes["service:pay"].props.service_name).toBe("pay");
    expect(s.nodes["service:pay"].created_by).toBe(1);
    expect(hypothesisList(s)).toHaveLength(1);
    expect(s.turns).toHaveLength(1);
  });

  it("reopen (no live events) rebuilds the conversation from the journal — the durable record", () => {
    // A disk-reopened investigation carries no event stream; the journal is the record.
    const snapshot = {
      session_id: "app-incident:INC-4821",
      subject: { domain: "app-incident", id: "INC-4821", kind: "incident" },
      state: "suspended",
      outcome: "open",
      phases: ["frame", "investigate", "investigate"],
      graph: { nodes: [], edges: [], facts: [], events: [] },
      hypotheses: [],
      journal: [
        { seq: 1, phase: "frame", actor: "engine", narrative: "payments-api 5xx spiked after the v4.12.0 deploy.", refs: {} },
        { seq: 2, phase: "investigate", actor: "engine", narrative: "The deploy is the prime suspect (H1).", refs: {} },
        { seq: 3, phase: "investigate", actor: "engine", narrative: "Ruled out the DB; NPE in TaxCalculator.", refs: {} },
      ],
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: null,
      messages: [],
      events: [], // reopened from disk — no live stream
    } as unknown as Snapshot;

    const s = reduce(emptyState(), { kind: "seed", snapshot });
    // the conversation is the full story, folded from the journal
    expect(s.turns).toHaveLength(3);
    expect(s.turns.map((t) => t.phase)).toEqual(["frame", "investigate", "investigate"]);
    expect(s.turns[0].reasoning).toContain("payments-api 5xx");
    expect(s.turns[2].reasoning).toContain("TaxCalculator");
    // the stepper seeds too: phasesRun stays unique, phaseCounts carries the ×2 investigate loop
    expect(s.phasesRun).toEqual(["frame", "investigate"]);
    expect(phaseCounts(s).investigate).toBe(2);
  });

  it("reopen renders the WHOLE story from the kind-tagged journal — tool calls + gate + decision + tail", () => {
    // The bundle now serves every journal kind whole; a reopened run must rebuild the tool-call
    // cards (from invocation entries sharing the phase seq), the write-gate (from gate_opened),
    // the operator decision (gate_decision), and the chat tail (message) — identical to live.
    const snapshot = {
      session_id: "app-incident:INC-4821",
      subject: { domain: "app-incident", id: "INC-4821", kind: "incident" },
      state: "closed",
      outcome: "resolved",
      phases: ["frame", "investigate", "act", "close"],
      graph: {
        nodes: [{ id: "service:pay", type: "service", props: {} }, { id: "anomaly:a1", type: "anomaly", props: {} }],
        edges: [],
        facts: [{ id: "f1", subject: "service:pay", predicate: "red_errors", value: 0.4, unit: null, at: "t", valid_to: null, source: "prometheus", state: "active" }],
        events: [],
      },
      hypotheses: [{ id: "hyp:h1", statement: "the deploy broke it", status: "confirmed", confidence: 0.9, basis: "b", root_candidate: "code_commit:abc", supporting: ["f1"], refuting: [], chain: [] }],
      journal: [
        { seq: 1, kind: "lifecycle", ts: "t1", phase: "frame", actor: "engine", event: "started" },
        { seq: 2, kind: "plan", ts: "t2", phase: "frame", actor: "engine", narrative: "frame plan", available: ["get_dependencies", "active_alerts"], plan_calls: ["get_dependencies", "active_alerts"], plan_ops: ["AddNode"] },
        { seq: 2, kind: "invocation", ts: "t2", phase: "frame", actor: "engine", intent: "get_dependencies", provider: "cmdb", effect: "read", outcome: "data", op_count: 3, blocked: false, reason: null, narrative: "map the topology", params: {} },
        { seq: 2, kind: "invocation", ts: "t2", phase: "frame", actor: "engine", intent: "active_alerts", provider: "prometheus", effect: "read", outcome: "empty", op_count: 0, blocked: false, reason: null, narrative: "read alerts", params: {} },
        { seq: 2, kind: "phase", ts: "t2", phase: "frame", actor: "engine", narrative: "5xx spiked after the deploy", goal: "frame the symptom", verdict: "advance", next_actions: [], refs: { nodes: ["service:pay", "anomaly:a1"], edges: [], facts: ["f1"], events: [], hypotheses: [] } },
        { seq: 3, kind: "phase", ts: "t3", phase: "investigate", actor: "engine", narrative: "Ruled out the DB; NPE in TaxCalculator", goal: "find the cause", verdict: "advance", next_actions: [], refs: { nodes: [], edges: [], facts: [], events: [], hypotheses: ["hyp:h1"] } },
        { seq: 4, kind: "gate_opened", ts: "t4", phase: "act", actor: "engine", intent: "rollback_release", narrative: "roll back to v4.11.3", gate_id: "g1", actions: [{ intent: "rollback_release", params: { to_version: "v4.11.3" }, provider: "ocp", effect: "write", summary: "rollback" }], hypothesis: "hyp:h1", evidence: ["f1"] },
        { seq: 5, kind: "gate_decision", ts: "t5", phase: "act", actor: "alice@oncall", source: "human", decision: "approve", intent: "rollback_release", narrative: "ship it", action: { gate_id: "g1", intent: "rollback_release", params: {} }, observation: { decision: "approve", actor: "alice@oncall" } },
        { seq: 6, kind: "lifecycle", ts: "t6", phase: "act", actor: "engine", event: "resumed" },
        { seq: 6, kind: "invocation", ts: "t6", phase: "act", actor: "engine", intent: "rollback_release", provider: "ocp", effect: "write", outcome: "data", op_count: 1, blocked: false, reason: null, narrative: "apply the rollback", params: { to_version: "v4.11.3" } },
        { seq: 6, kind: "phase", ts: "t6", phase: "act", actor: "engine", narrative: "applied the rollback under the gate", goal: "remediate", verdict: "advance", next_actions: [], refs: { nodes: [], edges: [], facts: [], events: [], hypotheses: [] } },
        { seq: 7, kind: "message", ts: "t7", phase: "act", actor: "operator", source: "human", narrative: "looks good, thanks", action: { kind: "steer" }, observation: { actor: "operator" } },
        { seq: 8, kind: "lifecycle", ts: "t8", phase: "close", actor: "engine", event: "closed", outcome: "resolved" },
        { seq: 8, kind: "phase", ts: "t8", phase: "close", actor: "engine", narrative: "postmortem: rollback resolved it", goal: "close", verdict: "done", next_actions: [], refs: { nodes: [], edges: [], facts: [], events: [], hypotheses: [] } },
      ],
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: null,
      messages: [],
      events: [],
    } as unknown as Snapshot;

    const s = reduce(emptyState(), { kind: "seed", snapshot });

    // one turn per phase entry, in seq order
    expect(s.turns.map((t) => t.phase)).toEqual(["frame", "investigate", "act", "close"]);
    // the FRAME turn rebuilt its tool-call cards from the invocation entries (intent/provider/outcome)
    const frame = s.turns[0];
    expect(frame.reasoning).toContain("5xx spiked");
    expect(frame.calls.map((c) => c.intent)).toEqual(["get_dependencies", "active_alerts"]);
    expect(frame.calls[0].provider).toBe("cmdb");
    expect(frame.calls[0].outcome).toBe("data");
    expect(frame.calls[1].outcome).toBe("empty");
    expect(frame.calls[0].startedAt).toBe("t2"); // the journal ts is the call's WHEN on reopen
    // obs hydrated from the phase entry's refs
    expect(frame.obs.factIds).toContain("f1");
    expect(frame.obs.nodeIds).toHaveLength(2);
    // the ACT turn carries the write it applied + the gate
    const act = s.turns.find((t) => t.phase === "act")!;
    expect(act.calls.map((c) => c.intent)).toEqual(["rollback_release"]);
    expect(act.gateId).toBe("g1");
    // tool-call keys are UNIQUE across the reopened turns (invocations share their phase seq)
    const keys = s.turns.flatMap((t) => t.calls.map((c) => c.seq));
    expect(new Set(keys).size).toBe(keys.length);
    // the write-gate was reconstructed with its hydrated hypothesis + evidence
    expect(s.gates["g1"].hypothesis?.statement).toBe("the deploy broke it");
    expect(s.gates["g1"].evidence[0].predicate).toBe("red_errors");
    expect(s.gates["g1"].actions[0].intent).toBe("rollback_release");
    // the operator DECISION (WHO approved) folded from gate_decision
    expect(s.decisions["g1"].decision).toBe("approve");
    expect(s.decisions["g1"].actor).toBe("alice@oncall");
    expect(s.decisions["g1"].source).toBe("human");
    // the operator chat tail folded from the message entry
    expect(s.messages.map((m) => m.text)).toEqual(["looks good, thanks"]);
    // the stepper collapses the loop; phasesRun stays unique
    expect(s.phasesRun).toEqual(["frame", "investigate", "act", "close"]);
  });

  it("reopen of a SUSPENDED run synthesizes the gated turn + carries the pending gate", () => {
    // A run reopened while suspended never completed the gated phase (no phase entry for it), so
    // the gate's turn is synthesized from the gate_opened record — as live's phase_started does.
    const pendingGate = {
      type: "gate_opened", seq: 4, ts: "t4", gate_id: "g1", phase: "act",
      reasoning: "roll back to v4.11.3", actions: [{ intent: "rollback_release", params: {}, provider: "ocp", effect: "write", summary: "rollback" }],
      hypothesis: { id: "hyp:h1", statement: "the deploy broke it", status: "supported", confidence: 0.9, root_candidate: null }, evidence: [],
    };
    const snapshot = {
      session_id: "app-incident:INC-4821",
      subject: { domain: "app-incident", id: "INC-4821", kind: "incident" },
      state: "suspended",
      outcome: "open",
      phases: ["frame", "investigate", "act"],
      graph: { nodes: [], edges: [], facts: [], events: [] },
      hypotheses: [{ id: "hyp:h1", statement: "the deploy broke it", status: "supported", confidence: 0.9, basis: "b", root_candidate: null, supporting: [], refuting: [], chain: [] }],
      journal: [
        { seq: 1, kind: "lifecycle", ts: "t1", phase: "frame", actor: "engine", event: "started" },
        { seq: 2, kind: "phase", ts: "t2", phase: "frame", actor: "engine", narrative: "5xx spiked", goal: "frame", verdict: "advance", next_actions: [], refs: {} },
        { seq: 3, kind: "phase", ts: "t3", phase: "investigate", actor: "engine", narrative: "deploy is the suspect", goal: "investigate", verdict: "advance", next_actions: [], refs: {} },
        { seq: 4, kind: "gate_opened", ts: "t4", phase: "act", actor: "engine", intent: "rollback_release", narrative: "roll back to v4.11.3", gate_id: "g1", actions: [{ intent: "rollback_release", params: {}, provider: "ocp", effect: "write", summary: "rollback" }], hypothesis: "hyp:h1", evidence: [] },
      ],
      postmortem: { root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] }, ruled_out: [], contributing: [], timeline: [], narrative: [] },
      pending_gate: pendingGate,
      messages: [],
      events: [],
    } as unknown as Snapshot;

    const s = reduce(emptyState(), { kind: "seed", snapshot });
    // the act turn was synthesized (no act phase entry existed) and carries the gate
    const act = s.turns.find((t) => t.phase === "act")!;
    expect(act).toBeTruthy();
    expect(act.gateId).toBe("g1");
    expect(act.reasoning).toContain("roll back");
    // the currently-open gate is carried so ApprovalCard renders live
    expect(s.gate?.gate_id).toBe("g1");
    expect(s.gates["g1"].hypothesis?.statement).toBe("the deploy broke it");
    expect(s.turns.map((t) => t.phase)).toEqual(["frame", "investigate", "act"]);
  });
});
