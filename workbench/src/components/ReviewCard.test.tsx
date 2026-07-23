import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ReviewCard from "./ReviewCard";
import type { PhaseReviewOpenedEvent } from "../types";

const REVIEW: PhaseReviewOpenedEvent = {
  type: "phase_review_opened",
  seq: 3,
  ts: "t",
  review_id: "r1",
  phase: "frame",
  to_phase: "investigate",
  summary: "'frame' is complete — proposing to advance to 'investigate'.",
  narrative: "payments-api 5xx spiked after the deploy",
  verdict: "advance",
  discovered: { facts: 3, nodes: 2, events: 1, edges: 1, hypotheses: 1 },
  hypothesis: { id: "hyp:h1", statement: "the deploy broke it", status: "proposed", confidence: 0.5, root_candidate: "code_commit:abc" },
  facts: ["f1"],
  nodes: ["service:pay"],
};

describe("ReviewCard — the phase-review direction approval", () => {
  afterEach(() => cleanup());

  it("renders the summary + the transition and offers approve / refine / deny", () => {
    render(<ReviewCard review={REVIEW} busy={false} onDecide={() => {}} />);
    expect(screen.getByText(/Direction check/)).toBeTruthy();
    expect(screen.getByText("frame → investigate")).toBeTruthy();
    expect(screen.getByText(/proposing to advance to 'investigate'/)).toBeTruthy();
    expect(screen.getByText(/discovered 2 nodes · 3 facts · moved 1 hypothesis/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Refine/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Deny/ })).toBeTruthy();
  });

  it("approve fires onDecide('approve')", () => {
    const onDecide = vi.fn();
    render(<ReviewCard review={REVIEW} busy={false} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve/ }));
    expect(onDecide).toHaveBeenCalledWith("approve", {});
  });

  it("refine collects a steer and fires onDecide('refine', {text})", () => {
    const onDecide = vi.fn();
    render(<ReviewCard review={REVIEW} busy={false} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: /Refine/ }));
    fireEvent.change(screen.getByPlaceholderText(/Steer the agent/), { target: { value: "check the cache tier" } });
    fireEvent.click(screen.getByRole("button", { name: /Re-run with steer/ }));
    expect(onDecide).toHaveBeenCalledWith("refine", { text: "check the cache tier" });
  });

  it("renders a decided chip once the direction was answered", () => {
    render(
      <ReviewCard
        review={REVIEW}
        decision={{ decision: "approve", actor: "alice@oncall", source: "human" }}
        busy={false}
        onDecide={() => {}}
      />
    );
    expect(screen.getByText(/Approved — advanced/)).toBeTruthy();
    expect(screen.getByText("frame → investigate")).toBeTruthy();
    // the action buttons are gone once decided
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });
});
