import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import LiveGraph from "./LiveGraph";
import { emptyState, reduce } from "../lib/store";
import { resetServedDictionary, setServedDictionary } from "../lib/labels";
import type { SessionEvent, Snapshot } from "../types";

// Provisional (P3 airlock) knowledge must READ as tentative in the graph: a provisional
// edge renders dim + dotted, distinct from settled structural/causal edges.
describe("LiveGraph — provisional rendering", () => {
  afterEach(() => cleanup());

  it("dims a provisional edge and keeps a settled edge solid", () => {
    const events: SessionEvent[] = [
      { seq: 1, ts: "t", type: "phase_started", phase: "investigate" },
      {
        seq: 2,
        ts: "t",
        type: "graph_delta",
        nodes: [
          { id: "service:pay", type: "service", created_by: 1 },
          { id: "database:orders", type: "database", created_by: 1 },
          { id: "generic_ci:lb-7", type: "generic_ci", created_by: 1 },
        ],
        edges: [
          { id: "e-settled", type: "reads_from", src: "service:pay", dst: "database:orders", origin: "declared" },
          {
            id: "e-prov",
            type: "connects_to",
            src: "service:pay",
            dst: "generic_ci:lb-7",
            origin: "discovered",
            provisional: true,
          },
        ],
        facts: [],
        events: [],
      },
    ];
    const live = reduce(emptyState(), { kind: "events", events });
    render(<LiveGraph live={live} selection={null} onSelect={() => {}} />);
    const provisional = document.querySelectorAll(".edge--provisional");
    expect(provisional.length).toBe(1);
    expect(provisional[0].getAttribute("stroke-opacity")).toBe("0.45");
    // exactly two visible edges total, and the settled one is not dimmed
    const edges = document.querySelectorAll(".edge");
    expect(edges.length).toBe(2);
    const settled = Array.from(edges).find((e) => !e.classList.contains("edge--provisional"));
    expect(settled?.getAttribute("stroke-opacity")).toBeNull();
  });
});

// ── node-detail renders the six datum-shape categories (2026-07-23 primitives §2) ─────────────
const SVC = "service:payments-api|prod";

function nodeDetailSnapshot(): Snapshot {
  return {
    session_id: "s1",
    state: "closed",
    subject: { domain: "app-incident", id: "INC-1", kind: "incident" },
    outcome: "resolved",
    phases: ["frame"],
    // the engine-served species + identity_keys the categorizer reads (the whole point of the fix)
    dictionary: {
      predicates: {},
      relations: {},
      intents: {},
      species: { degraded: "state", error_rate: "reading", latency_p99: "reading" },
      identity_keys: { service: ["service_name", "env"] },
    },
    graph: {
      nodes: [
        {
          id: SVC,
          type: "service",
          props: {
            service_name: "payments-api",
            env: "prod",
            owner: "payments-platform",
            work_notes: "[09:02] oncall: High5xxRate paged; v4.12 suspect\n[09:26] oncall: rollback staged",
          },
          origin: true,
        },
      ],
      edges: [],
      facts: [
        // STATE trail: degraded true (superseded) → false (current)
        { id: "f-deg1", subject: SVC, predicate: "degraded", value: true, unit: null,
          at: "2026-07-19T09:04:00Z", valid_to: "2026-07-19T09:40:00Z", source: "prometheus", state: "superseded" },
        { id: "f-deg2", subject: SVC, predicate: "degraded", value: false, unit: null,
          at: "2026-07-19T09:40:00Z", valid_to: null, source: "prometheus", state: "active" },
        // READINGs
        { id: "f-err", subject: SVC, predicate: "error_rate", value: 0.4, unit: "ratio",
          at: "2026-07-19T09:04:00Z", valid_to: null, source: "prometheus", state: "active" },
        { id: "f-lat", subject: SVC, predicate: "latency_p99", value: 812, unit: "ms",
          at: "2026-07-19T09:04:00Z", valid_to: null, source: "appd", state: "active" },
      ],
      events: [
        { id: "ev1", entity: SVC, type: "degraded_cleared", at: "2026-07-19T09:40:00Z", payload: {}, source: "prometheus" },
      ],
      spans: [
        { id: "span:1", subject: SVC, name: "trace", value: { error: true }, unit: null,
          started_at: "2026-07-19T09:04:00Z", ended_at: "2026-07-19T09:04:00.840Z",
          span_phase: "closed", correlation_id: "trace-pay-42", source: "appd" },
      ],
    },
    hypotheses: [],
    journal: [],
    postmortem: { root_cause: null, ruled_out: [], contributing: [], timeline: [], narrative: [] },
    pending_gate: null,
    pending_review: null,
    messages: [],
    events: [],
  };
}

describe("LiveGraph — node-detail: the six datum-shape categories", () => {
  afterEach(() => {
    cleanup();
    resetServedDictionary();
  });

  function renderDetail() {
    const snap = nodeDetailSnapshot();
    setServedDictionary(snap.dictionary);
    const live = reduce(emptyState(), { kind: "seed", snapshot: snap });
    render(<LiveGraph live={live} selection={{ kind: "node", id: SVC }} onSelect={() => {}} />);
    return document.querySelector(".node-detail") as HTMLElement;
  }

  it("splits props into IDENTITY vs PROPERTY by the served identity_keys", () => {
    const detail = renderDetail();
    const identity = within(detail).getByText("Identity").closest(".cat") as HTMLElement;
    expect(within(identity).getByText("payments-api")).toBeTruthy(); // identity key value
    const property = within(detail).getByText("Property").closest(".cat") as HTMLElement;
    expect(within(property).getByText("payments-platform")).toBeTruthy(); // non-identity prop
    // the identity key value does NOT leak into the Property card
    expect(within(property).queryByText("payments-api")).toBeNull();
  });

  it("renders the STATE change-trail with both held values (true → false)", () => {
    const detail = renderDetail();
    const state = within(detail).getByText("State").closest(".cat") as HTMLElement;
    expect(within(state).getByText("change-trail")).toBeTruthy();
    // current value false + a badge, and the trail carries the superseded 'true'
    expect(within(state).getByText("now")).toBeTruthy();
    expect(within(state).getByText("true")).toBeTruthy();
    expect(within(state).getAllByText("false").length).toBeGreaterThan(0);
    // the superseded step reads struck-through
    expect(state.querySelector(".trail__steps li.is-superseded")).toBeTruthy();
  });

  it("routes metrics to READINGS and does not mix them into State", () => {
    const detail = renderDetail();
    const readings = within(detail).getByText(/^Readings/).closest(".cat") as HTMLElement;
    expect(within(readings).getByText("error rate")).toBeTruthy();
    expect(within(readings).getByText("latency p99")).toBeTruthy();
    // degraded (a STATE) is NOT in the readings card
    expect(within(readings).queryByText("degraded")).toBeNull();
  });

  it("renders the SPAN with its phase, duration and correlation id (§2.6)", () => {
    const detail = renderDetail();
    const spans = within(detail).getByText(/^Spans/).closest(".cat") as HTMLElement;
    expect(within(spans).getByText("closed")).toBeTruthy();
    expect(within(spans).getByText(/840 ms/)).toBeTruthy();
    expect(within(spans).getByText(/trace-pay-42/)).toBeTruthy();
  });

  it("renders the EVENT journal and the work-notes journal", () => {
    const detail = renderDetail();
    const events = within(detail).getByText(/^Events/).closest(".cat") as HTMLElement;
    expect(within(events).getByText("degraded_cleared")).toBeTruthy();
    const notes = within(detail).getByText(/^Work notes/).closest(".cat") as HTMLElement;
    // two independently-timestamped human notes, each its own journal entry
    expect(notes.querySelectorAll(".node-detail__journal li").length).toBe(2);
    expect(within(notes).getByText(/High5xxRate paged/)).toBeTruthy();
  });
});
