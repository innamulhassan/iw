import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { Snapshot } from "../types";
import { useInvestigation } from "./useInvestigation";

// ── a controllable EventSource double (jsdom ships none) ───────────────────────────
type Listener = (e: MessageEvent) => void;

class MockEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: MockEventSource[] = [];
  url: string;
  readyState = MockEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, Listener[]>();
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.closed = true;
    this.readyState = MockEventSource.CLOSED;
  }
  /** test driver: the connection established */
  open() {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.();
  }
  /** test driver: deliver one server event frame */
  emit(type: string, data: unknown) {
    for (const fn of this.listeners.get(type) ?? []) {
      fn({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
  /** test driver: terminal failure (e.g. proxy 502) — readyState CLOSED, no native retry */
  fail() {
    this.readyState = MockEventSource.CLOSED;
    this.onerror?.();
  }
}

const last = () => MockEventSource.instances[MockEventSource.instances.length - 1];

// ── fetch routing: snapshot GET for openExisting/reconcile, POST /advance for step ──
const SNAPSHOT = {
  session_id: "app-incident:INC-1",
  subject: { domain: "app-incident", id: "INC-1", kind: "incident" },
  state: "running",
  outcome: "open",
  phases: ["frame"],
  graph: { nodes: [], edges: [], facts: [], events: [] },
  ledger: [],
  journal: [],
  postmortem: {
    root_cause: { statement: "", root_candidate: null, confidence: 0, chain: [] },
    ruled_out: [],
    contributing: [],
    timeline: [],
    narrative: [],
  },
  pending_gate: null,
  messages: [],
  events: [
    { seq: 1, ts: "t", type: "phase_started", phase: "frame" },
    { seq: 2, ts: "t", type: "reasoning", phase: "frame", narrative: "looking" },
  ],
} as unknown as Snapshot;

let advanceResponse: { ok: boolean; status?: number; statusText?: string; json: () => Promise<unknown> };

function routeFetch(url: string) {
  if (url.includes("/advance")) return advanceResponse;
  if (/\/sessions\/[^/]+$/.test(url)) return { ok: true, json: async () => SNAPSHOT };
  return { ok: false, status: 404, statusText: "not found", json: async () => ({ detail: "not found" }) };
}

async function openHook() {
  const hook = renderHook(() => useInvestigation());
  await act(async () => {
    await hook.result.current.openExisting("app-incident:INC-1");
  });
  expect(MockEventSource.instances).toHaveLength(1);
  return hook;
}

beforeEach(() => {
  MockEventSource.instances = [];
  advanceResponse = { ok: false, status: 500, statusText: "unset", json: async () => ({ detail: "unset" }) };
  vi.stubGlobal("EventSource", MockEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => Promise.resolve(routeFetch(String(input)) as Response))
  );
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useInvestigation — SSE reconnect (review finding 3)", () => {
  it("re-subscribes after a terminal stream error, resuming from the latest APPLIED seq", async () => {
    const { result } = await openHook();
    const es0 = MockEventSource.instances[0];
    expect(es0.url).toContain("after=2"); // initial subscribe uses the snapshot cursor

    // a live delta moves the cursor past the snapshot seq
    act(() => {
      es0.open();
      es0.emit("reasoning", { seq: 5, ts: "t", type: "reasoning", phase: "frame", narrative: "more" });
    });
    expect(result.current.state.lastSeq).toBe(5);

    // the proxy dies mid-run: terminal CLOSED error, no native retry
    act(() => es0.fail());
    expect(es0.closed).toBe(true); // the dead source is closed, not leaked
    expect(MockEventSource.instances).toHaveLength(1); // reconnect is scheduled, not immediate

    act(() => {
      vi.advanceTimersByTime(1000); // first backoff step
    });
    expect(MockEventSource.instances).toHaveLength(2);
    const es1 = last();
    expect(es1.url).toContain("after=5"); // resumes from lastSeq, NOT the stale snapshot seq
    expect(result.current.error).toBeNull();

    // the resumed stream keeps folding events
    act(() => {
      es1.open();
      es1.emit("reasoning", { seq: 6, ts: "t", type: "reasoning", phase: "frame", narrative: "again" });
    });
    expect(result.current.state.lastSeq).toBe(6);
  });

  it("backs off 1s → 2s → 5s (capped), and a healthy reconnect resets the budget", async () => {
    await openHook();

    // 1st retry after exactly 1s
    act(() => last().fail());
    act(() => void vi.advanceTimersByTime(999));
    expect(MockEventSource.instances).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(1));
    expect(MockEventSource.instances).toHaveLength(2);

    // 2nd retry after exactly 2s
    act(() => last().fail());
    act(() => void vi.advanceTimersByTime(1999));
    expect(MockEventSource.instances).toHaveLength(2);
    act(() => void vi.advanceTimersByTime(1));
    expect(MockEventSource.instances).toHaveLength(3);

    // 3rd retry after exactly 5s (the cap)
    act(() => last().fail());
    act(() => void vi.advanceTimersByTime(4999));
    expect(MockEventSource.instances).toHaveLength(3);
    act(() => void vi.advanceTimersByTime(1));
    expect(MockEventSource.instances).toHaveLength(4);

    // a successful open resets the backoff: the next drop retries after 1s again
    act(() => last().open());
    act(() => last().fail());
    act(() => void vi.advanceTimersByTime(1000));
    expect(MockEventSource.instances).toHaveLength(5);
  });

  it("gives up after the max attempts and surfaces a visible error", async () => {
    const { result } = await openHook();

    const delays = [1000, 2000, 5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000]; // 10 attempts
    for (const [i, delay] of delays.entries()) {
      act(() => last().fail());
      act(() => void vi.advanceTimersByTime(delay));
      expect(MockEventSource.instances).toHaveLength(i + 2); // one new source per attempt
      expect(result.current.error).toBeNull(); // still trying — no error yet
    }

    act(() => last().fail()); // 11th consecutive failure: budget exhausted
    expect(result.current.error).toMatch(/reconnect/i);
    act(() => void vi.advanceTimersByTime(600_000));
    expect(MockEventSource.instances).toHaveLength(11); // no further attempts
  });

  it("does not reconnect once the session is closed", async () => {
    const { result } = await openHook();
    const es0 = MockEventSource.instances[0];
    await act(async () => {
      es0.emit("session_state", {
        seq: 7,
        ts: "t",
        type: "session_state",
        state: "closed",
        phase: null,
        outcome: "resolved",
      });
    });
    act(() => es0.fail()); // the stream ending with the session is not a failure
    act(() => void vi.advanceTimersByTime(600_000));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it("cancels a pending reconnect on unmount — no leaked timers or sources", async () => {
    const { unmount } = await openHook();
    act(() => last().fail()); // reconnect now pending
    unmount();
    act(() => void vi.advanceTimersByTime(600_000));
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("openExisting drops the previous session's stream and pending reconnect", async () => {
    const { result } = await openHook();
    act(() => last().fail()); // session A reconnect pending
    await act(async () => {
      await result.current.openExisting("app-incident:INC-1"); // switch (re-open) a session
    });
    expect(MockEventSource.instances).toHaveLength(2); // the fresh subscribe only
    const fresh = last();
    act(() => void vi.advanceTimersByTime(600_000)); // the old timer must be dead
    expect(MockEventSource.instances).toHaveLength(2);
    expect(last()).toBe(fresh);
  });
});
