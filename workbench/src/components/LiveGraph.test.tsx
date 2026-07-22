import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import LiveGraph from "./LiveGraph";
import { emptyState, reduce } from "../lib/store";
import type { SessionEvent } from "../types";

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
