import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

  it("renders a reasoning step's conclusion as a finding, and does not duplicate a fetch step's observation", () => {
    const t = turn({
      calls: [toolCall({ seq: 1, intent: "get_commit", todo: 0 })],
      todos: [
        { objective: "read the commit", plannedCalls: ["get_commit"], plannedOps: ["AddNode"], status: "pending", observation: "abc123 adds intl VAT regions" },
        { objective: "frame the rival hypotheses", plannedCalls: [], plannedOps: ["ProposeHypothesis", "ProposeHypothesis"], status: "pending", observation: "the deploy is the prime suspect (H1)" },
      ],
    });
    render(<ChatPane live={live(t)} busy={false} onDecide={noop} onReview={noop} onSend={noop} />);
    // the reasoning-only step surfaces its conclusion as a finding line + a "reasoning" tag
    expect(screen.getByText(/the deploy is the prime suspect \(H1\)/)).toBeTruthy();
    expect(screen.getByText("reasoning")).toBeTruthy();
    // the call-bearing step does NOT re-render its observation — the tool card carries the result
    expect(screen.queryByText("abc123 adds intl VAT regions")).toBeNull();
  });
});

// Full-window story mode — the chat can expand to the whole workbench for reading end to end.
describe("ChatPane — full-window toggle", () => {
  afterEach(() => cleanup());

  it("renders an accessible maximize button that toggles and reflects the expanded state", () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <ChatPane live={live(turn({}))} busy={false} onDecide={noop} onReview={noop} onSend={noop} expanded={false} onToggleExpand={onToggle} />
    );
    const btn = screen.getByRole("button", { name: /expand the chat to full window/i });
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
    // once expanded, the control offers to restore (aria reflects the pressed state)
    rerender(
      <ChatPane live={live(turn({}))} busy={false} onDecide={noop} onReview={noop} onSend={noop} expanded={true} onToggleExpand={onToggle} />
    );
    const restore = screen.getByRole("button", { name: /restore the workbench layout/i });
    expect(restore.getAttribute("aria-pressed")).toBe("true");
  });

  it("omits the toggle when no handler is wired (standalone render)", () => {
    render(<ChatPane live={live(turn({}))} busy={false} onDecide={noop} onReview={noop} onSend={noop} />);
    expect(screen.queryByRole("button", { name: /full window/i })).toBeNull();
  });
});
