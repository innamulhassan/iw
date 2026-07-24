import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PhaseStepper from "./PhaseStepper";
import type { Subject } from "../types";

// The header LAYER assertion is DISCOVERED, not assumed: while the engine hasn't confirmed a root
// the badge must read a muted "Layer — determining…" and NEVER pre-reveal the catalog's guess; once
// the root is confirmed it resolves to the earned layer.
describe("PhaseStepper — the discovered-not-assumed LAYER badge", () => {
  afterEach(() => cleanup());

  const subject: Subject = { domain: "app-incident", id: "INC-4821", kind: "incident" };
  const base = {
    subject,
    rail: [{ id: "frame", focus: true }],
    reached: ["frame"],
    current: "frame",
    state: "running",
    outcome: "open",
    onBack: () => {},
  };

  it("shows a muted 'determining…' badge while the layer is unproven (null)", () => {
    render(<PhaseStepper {...base} discoveredLayer={null} />);
    const badge = screen.getByText("Layer — determining…");
    expect(badge.className).toContain("phase-bar__layer--determining");
  });

  it("resolves to the EARNED layer once the engine confirms the root", () => {
    render(<PhaseStepper {...base} discoveredLayer="Application code" />);
    expect(screen.queryByText("Layer — determining…")).toBeNull();
    const badge = screen.getByText("Application code");
    // the resolved badge is the confident pill, not the muted determining variant
    expect(badge.className).toContain("phase-bar__layer");
    expect(badge.className).not.toContain("phase-bar__layer--determining");
  });
});
