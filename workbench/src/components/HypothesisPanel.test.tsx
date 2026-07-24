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

  it("shows the belief timestamp, a clickable root chip, and per-row supports/refutes stance", () => {
    const sel: unknown[] = [];
    const hyp = {
      ...fixtureBundle.hypotheses[0], // confirmed, root_candidate code_commit:deadbeef, supporting fact:test-1
      updated_at: "2026-07-19T14:27:00+00:00",
      refuting: ["fact:test-1"], // give it a refuting row too, so both stances render
    };
    const facts = { "fact:test-1": fixtureBundle.graph.facts[0] };
    render(
      <HypothesisPanel hypotheses={[hyp]} facts={facts} nodes={{}} selection={null} onSelect={(s) => sel.push(s)} />
    );
    fireEvent.click(document.querySelector(".hypothesis-card__toggle")!);

    // (B1) the "updated HH:MM" stamp (locale/TZ-agnostic — rendered in the viewer's local time)
    expect(screen.getByText(/^updated \d{1,2}:\d\d/)).toBeTruthy();

    // (B3) the root renders as a "root → <node>" chip that cross-highlights the node on click
    const rootChip = screen.getByRole("button", { name: /root →/ });
    fireEvent.click(rootChip);
    expect(sel).toContainEqual({ kind: "node", id: "code_commit:deadbeef" });

    // (B4) supporting vs refuting are marked per-row, not just on the group label
    expect(document.querySelector(".evrow__stance--supporting")?.textContent).toBe("supports");
    expect(document.querySelector(".evrow__stance--refuting")?.textContent).toBe("refutes");
    expect(document.querySelectorAll(".evrow--refuting").length).toBe(1);
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
