import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import Workbench from "./Workbench";
import { emptyState, reduce } from "../lib/store";
import type { SessionEvent } from "../types";

// The blank-page fix: while a NEW investigation spins up (reset → createSession → seed) the store
// is empty and carries no session id, so `busy && !sessionId` must render the "processing…" state
// instead of a blank workbench. Once the seed lands a session id + turns, the full panes render.
describe("Workbench — starting/processing state", () => {
  // jsdom has no scrollIntoView; the full workbench mounts ChatPane, which auto-scrolls on mount.
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => cleanup());

  const noop = () => {};

  it("shows the processing state while busy with no session yet (the blank-page fix)", () => {
    render(
      <Workbench
        live={emptyState()}
        busy={true}
        error={null}
        onDecide={noop}
        onReview={noop}
        onSend={noop}
        onBack={noop}
      />
    );
    expect(screen.getByText("Processing…")).toBeTruthy();
    // the heavy panes are NOT mounted while starting (no chat composer yet)
    expect(screen.queryByText("agent working…")).toBeNull();
  });

  it("does NOT show the processing state once a session has seeded (turns present)", () => {
    const seeded = { ...emptyState(), sessionId: "app-incident_INC-4821" };
    const live = reduce(seeded, {
      kind: "events",
      events: [{ seq: 1, ts: "t", type: "phase_started", phase: "frame" }] as SessionEvent[],
    });
    render(
      <Workbench
        live={live}
        busy={true}
        error={null}
        onDecide={noop}
        onReview={noop}
        onSend={noop}
        onBack={noop}
      />
    );
    // a real session id is set, so the full workbench renders — never the blank/processing gap
    expect(screen.queryByText("Processing…")).toBeNull();
  });
});

// Per-panel maximize/minimize: each of chat/graph/hypotheses has its own controls. Maximize expands
// one panel full width (hiding the others, at most one at a time); minimize collapses one to a strip.
describe("Workbench — per-panel maximize/minimize layout", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => cleanup());

  const noop = () => {};

  // a seeded live state with a session id + a node so all three panels render their headers
  function seededLive() {
    const seeded = { ...emptyState(), sessionId: "s1" };
    return reduce(seeded, {
      kind: "events",
      events: [
        { seq: 1, ts: "t", type: "phase_started", phase: "frame" },
        {
          seq: 2, ts: "t", type: "graph_delta",
          nodes: [{ id: "service:x", type: "service", created_by: 1 }],
          edges: [], facts: [], events: [],
        },
      ] as SessionEvent[],
    });
  }

  function renderWorkbench() {
    return render(
      <Workbench live={seededLive()} busy={false} error={null} onDecide={noop} onReview={noop} onSend={noop} onBack={noop} />
    );
  }

  it("maximizing the graph hides the other two panels; restore brings them back", () => {
    renderWorkbench();
    // all three panels present in the default split
    expect(screen.getByText("Investigation chat")).toBeTruthy();
    expect(screen.getByText("Incident graph")).toBeTruthy();
    expect(screen.getByText("Hypotheses")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /maximize the graph panel/i }));
    // only the graph remains; its control now offers restore
    expect(screen.getByText("Incident graph")).toBeTruthy();
    expect(screen.queryByText("Investigation chat")).toBeNull();
    expect(screen.queryByText("Hypotheses")).toBeNull();
    expect(screen.queryByRole("button", { name: /maximize the chat panel/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /restore the graph panel/i }));
    // the split is back — all three panels render again
    expect(screen.getByText("Investigation chat")).toBeTruthy();
    expect(screen.getByText("Hypotheses")).toBeTruthy();
  });

  it("minimizing the hypotheses panel collapses it to a restorable strip (others stay)", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("button", { name: /minimize the hypotheses panel/i }));
    // the panel body is gone (no maximize control for it) but a restore strip remains; the other
    // two panels are untouched (minimize is independent, not a maximize)
    expect(screen.queryByRole("button", { name: /maximize the hypotheses panel/i })).toBeNull();
    expect(screen.getByRole("button", { name: /restore the hypotheses panel/i })).toBeTruthy();
    expect(screen.getByText("Investigation chat")).toBeTruthy();
    expect(screen.getByText("Incident graph")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /restore the hypotheses panel/i }));
    expect(screen.getByRole("button", { name: /maximize the hypotheses panel/i })).toBeTruthy();
  });
});
