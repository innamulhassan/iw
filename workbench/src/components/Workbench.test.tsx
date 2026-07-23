import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
