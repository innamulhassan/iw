import { useState } from "react";
import type { ToolCall } from "../lib/store";

// A capability call, shown collapsed by default (UI-SPEC §2): the tool + a one-line result,
// expandable to the provider / effect / op-count / block reason — like a tool-call card in an
// agent UI, not a flat wall of text. Carries the agent-trace span (obs 9): tool-vs-workflow
// KIND, WHEN it ran, and HOW LONG it took.

function fmtDuration(ms?: number | null): string | null {
  if (ms == null) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function fmtClock(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const isWrite = call.effect === "write";
  const status = call.blocked ? "blocked" : "ok";
  const dur = fmtDuration(call.durationMs);
  const started = fmtClock(call.startedAt);
  const kind = call.kind ?? (isWrite ? "workflow" : "tool");

  return (
    <div className={`toolcall toolcall--${status} ${isWrite ? "toolcall--write" : ""}`}>
      <button className="toolcall__summary" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`toolcall__chevron ${open ? "is-open" : ""}`}>▶</span>
        <span className="toolcall__icon" aria-hidden="true">
          {call.blocked ? "⛔" : isWrite ? "✍️" : "🔧"}
        </span>
        <code className="toolcall__intent">{call.intent}</code>
        <span className="toolcall__provider">{call.provider}</span>
        <span className={`toolcall__kind toolcall__kind--${kind}`}>{kind}</span>
        <span className="toolcall__result">
          {call.blocked ? "blocked" : `${call.op_count} op${call.op_count === 1 ? "" : "s"}`}
          {dur && <span className="toolcall__dur"> · {dur}</span>}
        </span>
      </button>
      {open && (
        <dl className="toolcall__detail">
          <div>
            <dt>intent</dt>
            <dd>
              <code>{call.intent}</code>
            </dd>
          </div>
          <div>
            <dt>provider</dt>
            <dd>{call.provider}</dd>
          </div>
          <div>
            <dt>kind · effect</dt>
            <dd>
              {kind} · {call.effect}
            </dd>
          </div>
          {started && (
            <div>
              <dt>when</dt>
              <dd>{started}</dd>
            </div>
          )}
          {dur && (
            <div>
              <dt>took</dt>
              <dd>{dur}</dd>
            </div>
          )}
          <div>
            <dt>result</dt>
            <dd>
              {call.blocked
                ? `blocked — ${call.reason ?? "no approved gate"}`
                : `folded ${call.op_count} graph operation${call.op_count === 1 ? "" : "s"}`}
            </dd>
          </div>
        </dl>
      )}
    </div>
  );
}
