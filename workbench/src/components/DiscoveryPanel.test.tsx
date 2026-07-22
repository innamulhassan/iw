import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import DiscoveryPanel from "./DiscoveryPanel";

describe("DiscoveryPanel — the airlock's promotion signal", () => {
  afterEach(() => cleanup());

  it("surfaces recurring class hints + quarantined names with their frequencies", () => {
    render(
      <DiscoveryPanel
        discovery={{
          class_hints: { LoadBalancer: 3, MessageBroker: 1 },
          quarantined_names: { "x.appd.weird_metric": 2 },
        }}
      />
    );
    expect(screen.getByText("Discovery")).toBeTruthy();
    expect(screen.getByText("LoadBalancer")).toBeTruthy();
    expect(screen.getByText("×3")).toBeTruthy();
    expect(screen.getByText("x.appd.weird_metric")).toBeTruthy();
    expect(screen.getByText("×2")).toBeTruthy();
    // highest frequency first
    const chips = Array.from(document.querySelectorAll(".discovery-chip__name")).map((el) => el.textContent);
    expect(chips.indexOf("LoadBalancer")).toBeLessThan(chips.indexOf("MessageBroker"));
    // the human owns promotion — the panel says so
    expect(screen.getByText(/Promotion is a human edit/)).toBeTruthy();
  });

  it("renders nothing when the engine saw no unknowns", () => {
    const { container } = render(
      <DiscoveryPanel discovery={{ class_hints: {}, quarantined_names: {} }} />
    );
    expect(container.firstChild).toBeNull();
  });
});
