import { useState } from "react";
import type { GateOpenedEvent } from "../types";
import type { Decision } from "../lib/store";
import type { GateDecision } from "../lib/api";

interface Props {
  gate: GateOpenedEvent;
  decision?: Decision;
  busy: boolean;
  onDecide: (d: GateDecision, opts: { params?: Record<string, unknown>; reason?: string }) => void;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const DECISION_LABEL: Record<GateDecision, string> = {
  approve: "Approved",
  refine: "Refined & approved",
  deny: "Denied",
};

// The human-in-the-loop write-gate (UI-SPEC §2), rendered inline in the chat. Approve applies
// the proposed write; Refine edits its params first; Deny drops it with a reason. "Why?" opens
// the recorded evidence — the serving hypothesis + its supporting facts.
export default function ApprovalCard({ gate, decision, busy, onDecide }: Props) {
  const action = gate.actions[0];
  const [why, setWhy] = useState(false);
  const [mode, setMode] = useState<"idle" | "refine" | "deny">("idle");
  const [params, setParams] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(action?.params ?? {}).map(([k, v]) => [k, formatValue(v)]))
  );
  const [reason, setReason] = useState("");
  const [instead, setInstead] = useState("");

  if (decision) {
    return (
      <div className={`approval approval--decided approval--${decision.decision}`}>
        <div className="approval__decided-row">
          <span className={`decision-chip decision-chip--${decision.decision}`}>{DECISION_LABEL[decision.decision]}</span>
          <span className="approval__decided-action">{action?.summary}</span>
        </div>
        {decision.reason && <p className="approval__reason">“{decision.reason}”</p>}
      </div>
    );
  }

  const coerce = (raw: string): unknown => {
    if (raw === "true") return true;
    if (raw === "false") return false;
    const n = Number(raw);
    return raw.trim() !== "" && !Number.isNaN(n) ? n : raw;
  };

  return (
    <div className="approval">
      <div className="approval__header">
        <span className="approval__badge">⚠︎ Decision needed — write-gate</span>
        <span className="approval__phase">{gate.phase}</span>
      </div>

      <p className="approval__ask">
        I want to run the action below to fix the incident. It changes a live system, so it needs
        your call: <strong>approve it</strong>, tweak it, decline it, or tell me to do something else.
      </p>

      {gate.reasoning && <p className="approval__reasoning">{gate.reasoning}</p>}

      <div className="approval__action">
        <span className="approval__action-label">
          Proposed action <span className="approval__recommended">recommended</span>
        </span>
        {gate.actions.map((a, i) => (
          <div key={i} className="approval__action-row">
            <span className={`toolcall__effect toolcall__effect--${a.effect}`}>{a.effect}</span>
            <code className="approval__action-intent">
              {a.provider}.{a.intent}
            </code>
            <span className="approval__action-summary">{formatValue(a.params.action ?? a.summary)}</span>
          </div>
        ))}
      </div>

      <button className="approval__why" onClick={() => setWhy((v) => !v)} aria-expanded={why}>
        {why ? "▾" : "▸"} why? (the evidence)
      </button>
      {why && (
        <div className="approval__evidence">
          {gate.hypothesis ? (
            <>
              <p className="approval__hyp">
                <span className={`badge badge--${gate.hypothesis.status}`}>{gate.hypothesis.status}</span>{" "}
                {gate.hypothesis.statement}
              </p>
              <ul className="approval__facts">
                {gate.evidence.map((f) => (
                  <li key={f.id}>
                    {f.predicate ? (
                      <>
                        <strong>{f.predicate}</strong> = {formatValue(f.value)}
                        {f.unit ? ` ${f.unit}` : ""} <span className="approval__fact-src">· {f.source}</span>
                      </>
                    ) : (
                      <code>{f.id}</code>
                    )}
                  </li>
                ))}
                {gate.evidence.length === 0 && <li className="approval__empty">No supporting facts recorded.</li>}
              </ul>
            </>
          ) : (
            <p className="approval__empty">No leading hypothesis on record for this gate.</p>
          )}
        </div>
      )}

      {mode === "refine" && (
        <div className="approval__refine">
          {Object.entries(params).map(([k, v]) => (
            <label key={k} className="approval__param">
              <span>{k}</span>
              <input value={v} onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))} />
            </label>
          ))}
        </div>
      )}

      {mode === "deny" && (
        <div className="approval__deny">
          <textarea
            placeholder="Why are you denying this remediation?"
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
              ✓ Approve <span className="btn__hint">recommended</span>
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
              className="btn btn--approve"
              disabled={busy}
              onClick={() =>
                onDecide("refine", {
                  params: Object.fromEntries(Object.entries(params).map(([k, v]) => [k, coerce(v)])),
                })
              }
            >
              ✓ Approve with changes
            </button>
            <button className="btn btn--ghost" disabled={busy} onClick={() => setMode("idle")}>
              Cancel
            </button>
          </>
        )}
        {mode === "deny" && (
          <>
            <button className="btn btn--deny" disabled={busy} onClick={() => onDecide("deny", { reason })}>
              ✕ Confirm deny
            </button>
            <button className="btn btn--ghost" disabled={busy} onClick={() => setMode("idle")}>
              Cancel
            </button>
          </>
        )}
      </div>

      {mode === "idle" && (
        <div className="approval__instead">
          <input
            className="approval__instead-input"
            value={instead}
            placeholder="Or tell me to do something else instead…"
            disabled={busy}
            onChange={(e) => setInstead(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && instead.trim()) onDecide("deny", { reason: instead.trim() });
            }}
          />
          <button
            className="btn btn--ghost"
            disabled={busy || !instead.trim()}
            onClick={() => onDecide("deny", { reason: instead.trim() })}
          >
            Send →
          </button>
        </div>
      )}
    </div>
  );
}
