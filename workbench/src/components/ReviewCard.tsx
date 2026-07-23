import { useState } from "react";
import type { PhaseReviewOpenedEvent } from "../types";
import type { Decision } from "../lib/store";
import type { GateDecision } from "../lib/api";

interface Props {
  review: PhaseReviewOpenedEvent;
  decision?: Decision;
  busy: boolean;
  onDecide: (d: GateDecision, opts: { text?: string }) => void;
}

const DECISION_LABEL: Record<GateDecision, string> = {
  approve: "Approved — advanced",
  refine: "Refined — re-ran the phase",
  deny: "Denied — halted",
};

// The phase-review gate (owner 2026-07-23), rendered inline in the chat. The agent finished a
// phase and asks the human to approve advancing to the next one: Approve advances · Refine re-runs
// the phase with a steer · Deny halts the investigation. "Why?" opens the leading hypothesis.
// This is the DIRECTION counterpart to ApprovalCard (which approves an irreversible write).
export default function ReviewCard({ review, decision, busy, onDecide }: Props) {
  const [why, setWhy] = useState(false);
  const [mode, setMode] = useState<"idle" | "refine" | "deny">("idle");
  const [steer, setSteer] = useState("");
  const [reason, setReason] = useState("");

  if (decision) {
    return (
      <div className={`approval approval--review approval--decided approval--${decision.decision}`}>
        <div className="approval__decided-row">
          <span className={`decision-chip decision-chip--${decision.decision}`}>{DECISION_LABEL[decision.decision]}</span>
          <span className="approval__decided-action">
            {review.phase} → {review.to_phase}
          </span>
        </div>
        {decision.reason && <p className="approval__reason">“{decision.reason}”</p>}
      </div>
    );
  }

  const d = review.discovered;

  return (
    <div className="approval approval--review">
      <div className="approval__header">
        <span className="approval__badge">◆ Direction check — advance?</span>
        <span className="approval__phase">
          {review.phase} → {review.to_phase}
        </span>
      </div>

      <p className="approval__ask">
        I finished <strong>{review.phase}</strong> and I'm ready to move on to{" "}
        <strong>{review.to_phase}</strong>. Approve to advance, refine to keep working this phase
        with a steer, or deny to stop here.
      </p>

      {review.narrative && <p className="approval__reasoning">{review.narrative}</p>}

      <div className="approval__action">
        <span className="approval__action-label">What this phase did</span>
        <div className="approval__action-row">
          <span className="toolcall__effect toolcall__effect--read">{review.verdict ?? "advance"}</span>
          <span className="approval__action-summary">{review.summary}</span>
        </div>
        {d && (
          <p className="review__discovered">
            discovered {d.nodes} node{d.nodes === 1 ? "" : "s"} · {d.facts} fact{d.facts === 1 ? "" : "s"} ·
            moved {d.hypotheses} hypothes{d.hypotheses === 1 ? "is" : "es"}
          </p>
        )}
      </div>

      {review.hypothesis && (
        <>
          <button className="approval__why" onClick={() => setWhy((v) => !v)} aria-expanded={why}>
            {why ? "▾" : "▸"} why? (the leading hypothesis)
          </button>
          {why && (
            <div className="approval__evidence">
              <p className="approval__hyp">
                <span className={`badge badge--${review.hypothesis.status}`}>{review.hypothesis.status}</span>{" "}
                {review.hypothesis.statement}
              </p>
              {review.hypothesis.root_candidate && (
                <p className="approval__facts">
                  root: <code>{review.hypothesis.root_candidate}</code>
                </p>
              )}
            </div>
          )}
        </>
      )}

      {mode === "refine" && (
        <div className="approval__deny">
          <textarea
            placeholder="Steer the agent — what should it look at before advancing?"
            value={steer}
            onChange={(e) => setSteer(e.target.value)}
            rows={2}
          />
        </div>
      )}

      {mode === "deny" && (
        <div className="approval__deny">
          <textarea
            placeholder="Why stop here? (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
          />
        </div>
      )}

      <div className="approval__actions">
        {mode === "idle" && (
          <>
            <button className="btn btn--approve" disabled={busy} onClick={() => onDecide("approve", {})}>
              ✓ Approve <span className="btn__hint">advance</span>
            </button>
            <button className="btn btn--refine" disabled={busy} onClick={() => setMode("refine")}>
              ✎ Refine
            </button>
            <button className="btn btn--deny" disabled={busy} onClick={() => setMode("deny")}>
              ✕ Deny
            </button>
          </>
        )}
        {mode === "refine" && (
          <>
            <button
              className="btn btn--refine"
              disabled={busy || !steer.trim()}
              onClick={() => onDecide("refine", { text: steer.trim() })}
            >
              ↻ Re-run with steer
            </button>
            <button className="btn btn--ghost" disabled={busy} onClick={() => setMode("idle")}>
              Cancel
            </button>
          </>
        )}
        {mode === "deny" && (
          <>
            <button className="btn btn--deny" disabled={busy} onClick={() => onDecide("deny", { text: reason.trim() })}>
              ✕ Confirm deny
            </button>
            <button className="btn btn--ghost" disabled={busy} onClick={() => setMode("idle")}>
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );
}
