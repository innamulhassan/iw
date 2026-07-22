import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RejectionsPanel from "./RejectionsPanel";

describe("RejectionsPanel — evidence withheld this run", () => {
  afterEach(() => cleanup());

  it("lists every dropped op with its phase, kind and reason", () => {
    render(
      <RejectionsPanel
        rejections={[
          { seq: 4, phase: "investigate", op_index: 2, op_kind: "AddFact", reason: "unknown predicate 'foo_bar'" },
          { seq: 9, phase: "verify", op_index: 0, op_kind: "AddEdge", reason: "unknown node ref" },
        ]}
      />
    );
    expect(screen.getByText("Ops dropped")).toBeTruthy();
    expect(screen.getByText(/2 ops the engine refused to fold/)).toBeTruthy();
    expect(screen.getByText("investigate")).toBeTruthy();
    expect(screen.getByText("AddFact")).toBeTruthy();
    expect(screen.getByText(/unknown predicate 'foo_bar'/)).toBeTruthy();
    expect(screen.getByText("verify")).toBeTruthy();
    expect(screen.getByText(/unknown node ref/)).toBeTruthy();
  });

  it("renders nothing when no ops were dropped", () => {
    const { container } = render(<RejectionsPanel rejections={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
