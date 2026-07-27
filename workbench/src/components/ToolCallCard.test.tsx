import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

// The honest capability trace (owner): the CALLABLE is provider-qualified, the PROTOCOL is the
// declared binding, and a MOCK must say it SIMULATES that protocol — it does NOT speak MCP/REST/A2A.
describe("ToolCallCard — capability trace honesty (callable · protocol · mock-mimes · timing)", () => {
  afterEach(() => cleanup());

  it("shows the provider-qualified callable name", () => {
    render(<ToolCallCard call={call({ provider: "servicenow", intent: "get_incident", outcome: "data", summary: "1 record" })} />);
    expect(screen.getByText("servicenow.get_incident")).toBeTruthy();
  });

  it("a MOCK call says it SIMULATES the protocol — never 'mock · mcp', never a fake '0ms'", () => {
    render(
      <ToolCallCard
        call={call({ provider: "servicenow", intent: "get_incident", servedBy: "mock", binding: "mcp", outcome: "data", durationMs: 0 })}
      />
    );
    // the honest served-by label + the declared-transport (protocol) badge
    expect(screen.getByText("MOCK · simulates MCP")).toBeTruthy();
    expect(screen.getByText("MCP")).toBeTruthy();
    // never the owner's misread ("mock uses mcp"), and never a misleading "0ms" for a simulated call
    expect(screen.queryByText(/mock · mcp/)).toBeNull();
    expect(screen.queryByText(/0ms/)).toBeNull();
    // expand → the trace reads "simulated · instant" and the via row spells out no real call is made
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/simulated · instant/)).toBeTruthy();
    expect(screen.getByText(/no real .*call is made/i)).toBeTruthy();
  });

  it("a LIVE call shows the real transport plainly and keeps its measured duration", () => {
    render(
      <ToolCallCard
        call={call({ provider: "prometheus", intent: "range_query", servedBy: "live", binding: "rest", outcome: "data", durationMs: 142 })}
      />
    );
    expect(screen.getByText("REST")).toBeTruthy(); // the declared protocol
    expect(screen.getByText(/live/)).toBeTruthy(); // the real transport, plainly (it DID call over REST)
    expect(screen.getByText(/142ms/)).toBeTruthy(); // the measured span survives for a live call
    expect(screen.queryByText(/simulates/)).toBeNull();
  });
});

// JOURNAL story fidelity: the WHY is the planner's OWN rationale (never a canned purpose when
// reasoning exists), the summary leads with the result LINE (not "N ops"), and a reasoned step that
// produced findings reads as "data" even when the mock transport outcome was "empty".
describe("ToolCallCard — the reasoned step's story", () => {
  afterEach(() => cleanup());

  it("shows the planner's own rationale as the why — not the hardcoded purpose", () => {
    render(
      <ToolCallCard
        call={call({
          intent: "get_incident", // PURPOSE map has "pull the incident record" — must NOT win
          rationale: "start from the incident of record — who paged, what tier is at risk",
          outcome: "empty",
        })}
      />
    );
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("start from the incident of record — who paged, what tier is at risk")).toBeTruthy();
    expect(screen.queryByText("pull the incident record")).toBeNull(); // the canned purpose is suppressed
  });

  it("leads the summary with the result line and reads as data despite an 'empty' transport outcome", () => {
    render(
      <ToolCallCard
        call={call({
          intent: "range_query",
          outcome: "empty", // the mock had no fixture — but the reasoned step produced real findings
          op_count: 13,
          result: "40% of ~820 rpm are 5xx; p50 holds at 58ms, p99 drags to 4.2s",
          produced: ["fact red_errors=0.4", "fact red_rate=820 rpm", "node anomaly ANOM-1"],
        })}
      />
    );
    // the summary shows the result, never "13 ops", and the attributed findings flip it to data
    expect(screen.getByText(/40% of ~820 rpm are 5xx/)).toBeTruthy();
    expect(document.querySelector(".toolcall--data")).toBeTruthy();
    expect(document.querySelector(".toolcall--empty")).toBeNull();
    expect(screen.queryByText(/no data — clean empty/)).toBeNull();
    // expand → the produced ops are itemized as the step's evidence
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("fact red_errors=0.4")).toBeTruthy();
    expect(screen.getByText("node anomaly ANOM-1")).toBeTruthy();
  });
});

// A FIXTURE transport must never claim it spoke to a real system, and the payload it replayed
// must be readable on the card — the two halves of "what actually happened, without leaving the
// screen".
describe("ToolCallCard — fixture provenance and raw evidence", () => {
  afterEach(() => cleanup());

  it("labels a `scenario`-served call as a fixture, not a live protocol call", () => {
    render(
      <ToolCallCard
        call={call({
          provider: "splunk",
          intent: "error_signature_topk",
          servedBy: "scenario",
          binding: "mcp",
          outcome: "data",
        })}
      />,
    );
    // ScenarioSource replays canned fixtures exactly as MockSource does. Testing for the literal
    // string "mock" missed it, so it rendered "📡 scenario — served live via scenario … a real MCP
    // call was made": a false statement on screen, over fixture data.
    expect(screen.queryByText(/📡\s*scenario/)).toBeNull();
    expect(screen.getByText(/MOCK/)).toBeTruthy();
  });

  it("still shows a genuinely live transport as live", () => {
    render(
      <ToolCallCard
        call={call({ provider: "prometheus", intent: "range_query", servedBy: "rest", binding: "rest", outcome: "data" })}
      />,
    );
    expect(screen.getByText(/📡\s*rest/)).toBeTruthy();
    expect(screen.queryByText(/MOCK/)).toBeNull();
  });

  it("expands to what the tool actually returned", () => {
    render(
      <ToolCallCard
        call={call({
          provider: "splunk",
          intent: "error_signature_topk",
          servedBy: "scenario",
          binding: "mcp",
          outcome: "data",
          result: "152 NPEs at TaxCalculator.java:88 since onset",
          raw: {
            errors: [
              {
                exception_class: "java.lang.NullPointerException",
                file_line: "TaxCalculator.java:88",
                trace_id: "tr-9f2a1",
              },
            ],
          },
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    // the summary line names the provider so the row reads as evidence, not decoration
    expect(screen.getByText(/what splunk actually returned/)).toBeTruthy();
    // and the payload itself is present verbatim — the stack-trace detail the summary drops
    expect(screen.getByText(/java\.lang\.NullPointerException/)).toBeTruthy();
    expect(screen.getByText(/tr-9f2a1/)).toBeTruthy();
  });

  it("renders no raw row when the call carried no payload", () => {
    render(
      <ToolCallCard
        call={call({ provider: "git", intent: "get_commit", servedBy: "mock", binding: "rest", outcome: "empty" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.queryByText(/actually returned/)).toBeNull();
  });
});
