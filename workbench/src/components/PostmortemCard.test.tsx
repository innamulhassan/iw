import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PostmortemCard from "./PostmortemCard";
import type { Postmortem } from "../types";

const RESOLVED: Postmortem = {
  outcome: "resolved",
  root_cause: {
    statement: "CHG-DB-500 dropped the orders index; the query planner fell back to a full scan.",
    root_candidate: "change_event:chg-db-500",
    confidence: 0.88,
    chain: [],
  },
  ruled_out: [
    { statement: "A code deploy regressed the endpoint", basis: "no deploy in the onset window" },
  ],
  contributing: [{ statement: "connection pool undersized for the scan load", confidence: 0.42 }],
  timeline: [
    { at: "2026-07-19T09:00:00+00:00", entity: "change_event:chg-db-500", type: "implemented", payload: {} },
    { at: "2026-07-19T09:06:00+00:00", entity: "anomaly:anom-6", type: "cleared", payload: {} },
  ],
  narrative: [
    { seq: 1, phase: "frame", text: "orders-api latency spiked after a DB migration." },
    { seq: 2, phase: "investigate", text: "Blame + diff pin the dropped index; the code rival is ruled out." },
  ],
};

describe("PostmortemCard — the served-but-unrendered close-out projection (M29)", () => {
  afterEach(() => cleanup());

  it("renders the confirmed root cause, its node + confidence, and the outcome", () => {
    render(<PostmortemCard postmortem={RESOLVED} outcome="resolved" />);
    expect(screen.getByText("Post-incident review")).toBeTruthy();
    expect(screen.getByText("resolved")).toBeTruthy();
    expect(screen.getByText(/CHG-DB-500 dropped the orders index/)).toBeTruthy();
    // the root node id appears on the root chip (and again in the timeline) — at least one
    expect(screen.getAllByText("chg-db-500").length).toBeGreaterThan(0);
    expect(screen.getByText("88% confidence")).toBeTruthy();
  });

  it("renders the ruled-out rivals WITH their basis (unique to this projection)", () => {
    render(<PostmortemCard postmortem={RESOLVED} outcome="resolved" />);
    expect(screen.getByText("A code deploy regressed the endpoint")).toBeTruthy();
    expect(screen.getByText(/no deploy in the onset window/)).toBeTruthy();
    expect(screen.getByText("Ruled out (1)")).toBeTruthy();
  });

  it("renders the structured timeline and per-phase narrative", () => {
    render(<PostmortemCard postmortem={RESOLVED} outcome="resolved" />);
    expect(screen.getByText("Timeline (2 events)")).toBeTruthy();
    expect(screen.getByText("Narrative (2 phases)")).toBeTruthy();
    expect(screen.getByText("09:00")).toBeTruthy(); // HH:MM, date stripped
    expect(screen.getAllByText("implemented").length).toBeGreaterThan(0);
    expect(screen.getByText(/Blame \+ diff pin the dropped index/)).toBeTruthy();
  });

  it("shows an honest 'no confirmed root cause' on a mitigated close (root_cause === null)", () => {
    const mitigated: Postmortem = { ...RESOLVED, outcome: "mitigated", root_cause: null };
    render(<PostmortemCard postmortem={mitigated} outcome="mitigated" />);
    expect(screen.getByText(/No confirmed root cause/)).toBeTruthy();
    expect(screen.getByText("mitigated")).toBeTruthy();
    // the ruled-out rivals still render — they are evidence regardless of confirmation
    expect(screen.getByText("A code deploy regressed the endpoint")).toBeTruthy();
  });
});
