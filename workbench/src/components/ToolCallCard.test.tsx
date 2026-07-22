import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ToolCallCard from "./ToolCallCard";
import type { ToolCall } from "../lib/store";

function call(overrides: Partial<ToolCall>): ToolCall {
  return {
    seq: 1,
    intent: "fetch_metrics",
    provider: "prometheus",
    effect: "read",
    op_count: 0,
    blocked: false,
    reason: null,
    ...overrides,
  };
}

// The invocation OUTCOME made visible (P3 boundary honesty): data vs clean-empty (an honest
// no-data read) vs error (a FAILED call — never "no data") vs blocked.
describe("ToolCallCard — outcome honesty", () => {
  afterEach(() => cleanup());

  it("renders a data call with its result summary", () => {
    render(<ToolCallCard call={call({ outcome: "data", op_count: 4, summary: "4 series folded" })} />);
    expect(screen.getByText(/4 series folded/)).toBeTruthy();
    expect(document.querySelector(".toolcall--data")).toBeTruthy();
    // a clean data call carries no outcome warning chip
    expect(document.querySelector(".toolcall__outcome")).toBeNull();
  });

  it("renders a clean-empty as HONEST no-data — not an error", () => {
    render(<ToolCallCard call={call({ outcome: "empty" })} />);
    expect(screen.getByText("empty")).toBeTruthy();
    expect(screen.getByText(/no data — clean empty/)).toBeTruthy();
    expect(document.querySelector(".toolcall--empty")).toBeTruthy();
  });

  it("renders an error as a FAILED call carrying no evidence — never 'no data'", () => {
    render(<ToolCallCard call={call({ outcome: "error", reason: "HTTP 503 from provider" })} />);
    expect(screen.getByText("error")).toBeTruthy();
    expect(screen.getByText(/call failed — HTTP 503 from provider · no evidence/)).toBeTruthy();
    expect(document.querySelector(".toolcall--error")).toBeTruthy();
    expect(screen.queryByText(/no data — clean empty/)).toBeNull();
  });

  it("renders a blocked write with its gate reason", () => {
    render(
      <ToolCallCard
        call={call({ intent: "apply_remediation", effect: "write", outcome: "blocked", blocked: true, reason: "no approved gate" })}
      />
    );
    expect(screen.getByText("blocked")).toBeTruthy();
    expect(screen.getByText(/blocked — no approved gate/)).toBeTruthy();
    expect(document.querySelector(".toolcall--blocked")).toBeTruthy();
  });

  it("legacy stream without an outcome field falls back to blocked-or-data", () => {
    render(<ToolCallCard call={call({ op_count: 3, summary: "3 ops" })} />);
    expect(document.querySelector(".toolcall--data")).toBeTruthy();
    expect(document.querySelector(".toolcall__outcome")).toBeNull();
  });
});
