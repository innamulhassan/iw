import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import ChatPane from "./ChatPane";
import { emptyState } from "../lib/store";
import type { LiveState, ToolCall, Turn } from "../lib/store";

// jsdom has no scrollIntoView; ChatPane auto-scrolls its transcript on mount.
beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

function toolCall(over: Partial<ToolCall>): ToolCall {
  return { seq: 1, intent: "x", provider: "p", effect: "read", op_count: 0, blocked: false, reason: null, outcome: "data", ...over };
}

function turn(over: Partial<Turn>): Turn {
  return {
    key: 1, phase: "frame", objective: "frame the symptom", reasoning: "5xx spiked",
    calls: [], obs: { factIds: [], nodeIds: [], edgeIds: [], eventIds: [], hypotheses: [] },
    rejections: [], ...over,
  };
}

function live(t: Turn): LiveState {
  return { ...emptyState(), sessionId: "s1", state: "closed", turns: [t] };
}

const noop = () => {};

// F1 — the chat PLAN section renders as a TO-DO CHECKLIST: each objective with its tool-call cards
// grouped under it and a status tick that reflects how far the to-do has executed.
describe("ChatPane — the to-do checklist (F1)", () => {
  afterEach(() => cleanup());

  it("renders the plan as a checklist, grouping each tool-call card under its to-do", () => {
    const t = turn({
      calls: [
        toolCall({ seq: 1, intent: "get_dependencies", provider: "cmdb", todo: 0, op_count: 3 }),
        toolCall({ seq: 2, intent: "active_alerts", provider: "prometheus", todo: 1, outcome: "empty" }),
      ],
      todos: [
        { objective: "map the topology", plannedCalls: ["get_dependencies"], plannedOps: ["AddNode"], status: "pending" },
        { objective: "read the alerts + propose", plannedCalls: ["active_alerts"], plannedOps: ["ProposeHypothesis"], status: "pending", delegate: true },
      ],
    });
    render(<ChatPane live={live(t)} busy={false} onDecide={noop} onReview={noop} onSend={noop} />);

    // both objectives render as checklist items
    expect(screen.getByText("map the topology")).toBeTruthy();
    expect(screen.getByText("read the alerts + propose")).toBeTruthy();

    // each tool card is grouped UNDER its own to-do (matched by ToolCall.todo)
    const items = document.querySelectorAll(".turn__todo");
    expect(items).toHaveLength(2);
    expect(within(items[0] as HTMLElement).getByText("get_dependencies")).toBeTruthy();
    expect(within(items[0] as HTMLElement).queryByText("active_alerts")).toBeNull();
    expect(within(items[1] as HTMLElement).getByText("active_alerts")).toBeTruthy();

    // the delegatable seam (F2) surfaces as a chip; the direct-op count shows too
    expect(screen.getByText(/delegatable/)).toBeTruthy();
    expect(screen.getAllByText("1 op").length).toBeGreaterThan(0);

    // the flat plan fold is SUPPRESSED when the checklist is present (no double render)
    expect(document.querySelector(".turn__plan")).toBeNull();
  });

  it("ticks each to-do by how far it has executed (done · active · pending · ops-only)", () => {
    const t = turn({
      calls: [
        toolCall({ seq: 1, intent: "get_dependencies", todo: 0 }), // to-do 0: 1/1 → done
        toolCall({ seq: 2, intent: "active_alerts", todo: 1 }), //     to-do 1: 1/2 → active
      ],
      todos: [
        { objective: "done one", plannedCalls: ["get_dependencies"], plannedOps: [], status: "pending" },
        { objective: "active one", plannedCalls: ["active_alerts", "fetch_metrics"], plannedOps: [], status: "pending" },
        { objective: "ops only", plannedCalls: [], plannedOps: ["ProposeHypothesis"], status: "pending" }, // vacuous → done
        { objective: "not started", plannedCalls: ["blame"], plannedOps: [], status: "pending" }, // 0/1 → pending
      ],
    });
    render(<ChatPane live={live(t)} busy={false} onDecide={noop} onReview={noop} onSend={noop} />);

    expect(document.querySelectorAll(".turn__todo--done")).toHaveLength(2); // the executed one + the ops-only
    expect(document.querySelectorAll(".turn__todo--active")).toHaveLength(1);
    expect(document.querySelectorAll(".turn__todo--pending")).toHaveLength(1);
  });

  it("falls back to flat tool-call cards when a turn carries no checklist (a bare live step)", () => {
    const t = turn({
      todos: undefined,
      calls: [toolCall({ seq: 1, intent: "get_dependencies", todo: null })],
    });
    render(<ChatPane live={live(t)} busy={false} onDecide={noop} onReview={noop} onSend={noop} />);
    expect(document.querySelector(".turn__todos")).toBeNull(); // no checklist
    expect(screen.getByText("get_dependencies")).toBeTruthy(); // the flat card still renders
  });
});
