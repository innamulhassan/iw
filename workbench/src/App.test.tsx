import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

const CATALOG = {
  incidents: [
    { id: "INC-4821", title: "payments-api 5xx after deploy", layer: "Application code", domain: "app-incident", kind: "incident" },
    { id: "INC-7734", title: "orders-api latency after index drop", layer: "Database", domain: "app-incident", kind: "incident" },
  ],
};

function routeFetch(url: string) {
  if (url.includes("/catalog")) return { ok: true, json: async () => CATALOG };
  if (url.includes("/sessions")) return { ok: true, json: async () => ({ sessions: [] }) };
  return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
}

describe("App start screen", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => Promise.resolve(routeFetch(String(input)) as Response))
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("renders the domain selector and a runnable incident card per layer from the catalog", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByText("Investigation Workbench")).toBeTruthy());

    // the catalog drives the runnable-incident cards (every layer)
    await waitFor(() => expect(screen.getByText("INC-4821")).toBeTruthy());
    expect(screen.getByText("INC-7734")).toBeTruthy();
    expect(screen.getByText("Application code")).toBeTruthy();
    expect(screen.getByText("Database")).toBeTruthy();

    // the incident-number input + start control exist
    expect(screen.getByPlaceholderText("e.g. INC-4821")).toBeTruthy();
    expect(screen.getByText("All incidents — every layer")).toBeTruthy();
  });
});
