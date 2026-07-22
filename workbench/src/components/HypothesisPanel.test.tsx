import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

  it("shows the engine-earned score prominently on every card", () => {
    render(
      <HypothesisPanel hypotheses={fixtureBundle.hypotheses} facts={{}} nodes={{}} selection={null} onSelect={() => {}} />
    );
    const scores = document.querySelectorAll(".hypothesis-card__score");
    expect(scores.length).toBe(2);
    expect(scores[0].textContent).toContain("85");
    expect(screen.getAllByText("earned").length).toBe(2);
  });

  it("renders in the ENGINE ranked order as given — never re-sorts client-side", () => {
    // engine-ranked order with the REFUTED one deliberately first: the old client-side
    // status-rank re-sort would have moved 'confirmed' to the top — the panel must not.
    const engineOrder = [...fixtureBundle.hypotheses].reverse(); // [refuted, confirmed]
    render(
      <HypothesisPanel hypotheses={engineOrder} facts={{}} nodes={{}} selection={null} onSelect={() => {}} />
    );
    const statements = Array.from(document.querySelectorAll(".hypothesis-card__statement")).map(
      (el) => el.textContent ?? ""
    );
    expect(statements[0]).toMatch(/network blip/);
    expect(statements[1]).toMatch(/commit deadbeef broke things/);
  });

  it("marks provisional (airlocked) evidence as tentative", () => {
    const facts = {
      "fact:test-1": { ...fixtureBundle.graph.facts[0], provisional: true },
    };
    render(
      <HypothesisPanel
        hypotheses={[fixtureBundle.hypotheses[0]]}
        facts={facts}
        nodes={{}}
        selection={null}
        onSelect={() => {}}
      />
    );
    // expand the card to reveal the evidence rows
    fireEvent.click(document.querySelector(".hypothesis-card__toggle")!);
    expect(screen.getByText("provisional")).toBeTruthy();
    expect(document.querySelector(".evrow.is-provisional")).toBeTruthy();
  });
});
