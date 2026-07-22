import type { InvestigationBundle } from "../types";

// A small, hand-written fixture matching the shape of a real engine bundle
// (see public/demo-code-regression.json), trimmed to just enough nodes,
// edges, facts, a hypotheses with one confirmed + one refuted hypothesis, and
// a couple of journal entries to exercise all four panes in tests.
export const fixtureBundle: InvestigationBundle = {
  subject: { domain: "app-incident", id: "INC-TEST-1", kind: "incident" },
  outcome: "resolved",
  phases: ["frame", "investigate", "investigate"],
  graph: {
    nodes: [
      {
        id: "service:test-api|prod",
        type: "service",
        props: { service_name: "test-api", env: "prod" },
      },
      { id: "anomaly:anom-t1", type: "anomaly", props: { anomaly_id: "ANOM-T1" } },
      { id: "code_commit:deadbeef", type: "code_commit", props: { sha: "deadbeef" } },
    ],
    edges: [
      {
        id: "edge:affects:anomaly:anom-t1->service:test-api|prod:discovered",
        type: "affects",
        src: "anomaly:anom-t1",
        dst: "service:test-api|prod",
        origin: "discovered",
        confidence: null,
      },
      {
        id: "edge:caused_by:hyp:t1->code_commit:deadbeef:inferred",
        type: "caused_by",
        src: "hyp:t1",
        dst: "code_commit:deadbeef",
        origin: "inferred",
        confidence: 0.8,
      },
    ],
    facts: [
      {
        id: "fact:test-1",
        subject: "service:test-api|prod",
        predicate: "red_errors",
        value: 0.02,
        unit: null,
        at: "2026-07-19T14:00:00+00:00",
        valid_to: null,
        source: "prometheus",
        state: "active",
      },
    ],
    events: [],
  },
  hypotheses: [
    {
      id: "hyp:t1",
      statement: "Test hypothesis: commit deadbeef broke things",
      status: "confirmed",
      confidence: 0.85,
      basis: "rollback fixed it",
      root_candidate: "code_commit:deadbeef",
      supporting: ["fact:test-1"],
      refuting: [],
      chain: [],
    },
    {
      id: "hyp:t2",
      statement: "Alternative theory: network blip",
      status: "refuted",
      confidence: 0.2,
      basis: "no network evidence found",
      root_candidate: null,
      supporting: [],
      refuting: ["fact:test-1"],
      chain: [],
    },
  ],
  journal: [
    {
      seq: 1,
      phase: "frame",
      actor: "engine",
      narrative: "test-api showed a small error spike.",
      refs: { nodes: [], edges: [], facts: [], events: [], hypotheses: [] },
    },
    {
      seq: 2,
      phase: "investigate",
      actor: "engine",
      narrative: "Proposed the leading hypothesis and a rival, then refuted the rival.",
      refs: { nodes: [], edges: [], facts: [], events: [], hypotheses: [] },
    },
  ],
  postmortem: {
    outcome: "resolved",
    root_cause: {
      statement: "Root cause: commit deadbeef introduced a regression in test-api.",
      root_candidate: "code_commit:deadbeef",
      confidence: 0.85,
      chain: [],
    },
    ruled_out: [{ statement: "Alternative theory: network blip", basis: "no network evidence found" }],
    contributing: [],
    timeline: [],
    narrative: [],
  },
};
