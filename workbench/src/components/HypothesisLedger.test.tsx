import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import HypothesisLedger from "./HypothesisLedger";
import { fixtureBundle } from "../test/fixture";

describe("HypothesisLedger", () => {
  afterEach(() => cleanup());

  it("shows confirmed and refuted hypotheses with both supporting and refuting evidence", () => {
    render(
      <HypothesisLedger ledger={fixtureBundle.ledger} facts={{}} selection={null} onSelect={() => {}} />
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
