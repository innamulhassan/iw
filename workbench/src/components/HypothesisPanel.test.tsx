import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import HypothesisPanel from "./HypothesisPanel";
import { fixtureBundle } from "../test/fixture";

describe("HypothesisPanel", () => {
  afterEach(() => cleanup());

  it("shows confirmed and refuted hypotheses with both supporting and refuting evidence", () => {
    render(
      <HypothesisPanel hypotheses={fixtureBundle.hypotheses} facts={{}} nodes={{}} selection={null} onSelect={() => {}} />
    );

    // Status badges for both sides of the story.
    expect(screen.getByText("Confirmed")).toBeTruthy();
    expect(screen.getByText("Refuted")).toBeTruthy();

    // Both hypothesis statements are present.
    expect(screen.getByText(/commit deadbeef broke things/)).toBeTruthy();
    expect(screen.getByText(/network blip/)).toBeTruthy();

    // Evidence counts render for each side.
    expect(screen.getByText(/1 supporting/)).toBeTruthy();
    expect(screen.getByText(/1 refuting/)).toBeTruthy();
  });
});
