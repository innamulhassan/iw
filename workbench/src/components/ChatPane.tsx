import { useEffect, useRef, useState } from "react";
import type { LiveState, Turn, UserMsg } from "../lib/store";
import type { GateDecision } from "../lib/api";
import ToolCallCard from "./ToolCallCard";
import ApprovalCard from "./ApprovalCard";

const PHASE_ICON: Record<string, string> = {
  frame: "🔭",
  triage: "🚑",
  hypothesize: "💡",
  investigate: "🔎",
  remediate: "🛠️",
  verify: "✅",
  close: "📓",
};

interface Props {
  live: LiveState;
  busy: boolean;
  onDecide: (gateId: string, d: GateDecision, opts: { params?: Record<string, unknown>; reason?: string }) => void;
  onSend: (text: string) => void;
}

type Item = { seq: number; kind: "turn"; turn: Turn } | { seq: number; kind: "msg"; msg: UserMsg };

// The primary interaction surface (UI-SPEC §2 / obs 2): a real two-way chat SESSION. The agent's
// per-phase turns (reasoning + collapsible tool-call cards + the write-gate) and the operator's
// own messages are interleaved by seq, and a composer lets the human steer, add context, or
// answer — while running or suspended.
export default function ChatPane({ live, busy, onDecide, onSend }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [live.turns.length, live.turns.at(-1)?.calls.length, live.messages.length, live.gate?.gate_id, live.state]);

  // interleave agent turns + operator messages by seq
  const items: Item[] = [
    ...live.turns.map((turn) => ({ seq: turn.key, kind: "turn" as const, turn })),
    ...live.messages.map((msg) => ({ seq: msg.seq + 0.5, kind: "msg" as const, msg })),
  ].sort((a, b) => a.seq - b.seq);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  };

  const canChat = live.sessionId != null && live.state !== "closed";
  const suspended = live.state === "suspended";

  return (
    <div className="chat">
      <div className="chat__header">
        <h2 className="pane-title">Investigation chat</h2>
        <p className="pane-subtitle">
          The agent reasons, calls tools, and pauses for your approval — steer it any time.
        </p>
      </div>

      <div className="chat__scroll">
        {items.map((it) =>
          it.kind === "msg" ? (
            <div key={`m${it.msg.seq}`} className="usermsg">
              <span className="usermsg__who">{it.msg.actor}</span>
              <p className="usermsg__text">{it.msg.text}</p>
            </div>
          ) : (
            (() => {
              const turn = it.turn;
              const gate = turn.gateId ? live.gates[turn.gateId] : undefined;
              const decision = turn.gateId ? live.decisions[turn.gateId] : undefined;
              const isOpenGate = gate && live.gate?.gate_id === gate.gate_id && !decision;
              return (
                <article key={`t${turn.key}`} className="turn">
                  <div className="turn__head">
                    <span className="turn__icon" aria-hidden="true">
                      {PHASE_ICON[turn.phase] ?? "•"}
                    </span>
                    <span className="turn__phase">{turn.phase}</span>
                  </div>
                  {turn.reasoning ? (
                    <p className="turn__reasoning">{turn.reasoning}</p>
                  ) : gate && !decision ? (
                    <p className="turn__reasoning turn__reasoning--muted">Proposing a remediation…</p>
                  ) : null}

                  {turn.calls.length > 0 && (
                    <div className="turn__calls">
                      {turn.calls.map((c) => (
                        <ToolCallCard key={c.seq} call={c} />
                      ))}
                    </div>
                  )}

                  {gate && (isOpenGate || decision) && (
                    <ApprovalCard
                      gate={gate}
                      decision={decision}
                      busy={busy}
                      onDecide={(d, opts) => onDecide(gate.gate_id, d, opts)}
                    />
                  )}
                </article>
              );
            })()
          )
        )}

        {live.error && <div className="chat__error">⚠ {live.error}</div>}
        {live.state === "closed" && (
          <div className="chat__closed">
            <span className="chat__closed-dot" /> Investigation closed — <strong>{live.outcome}</strong>
          </div>
        )}
        {live.state === "running" && busy && <div className="chat__typing">agent working…</div>}
        <div ref={endRef} />
      </div>

      {canChat && (
        <div className="chat__composer">
          <textarea
            className="chat__input"
            rows={1}
            value={draft}
            placeholder={suspended ? "Answer or steer the agent…" : "Steer the agent (e.g. “check the DB pool”, “ignore CHG-9”)…"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button className="chat__send" onClick={submit} disabled={!draft.trim()}>
            Send
          </button>
        </div>
      )}
    </div>
  );
}
