import { useEffect, useRef, useState } from "react";
import type { LiveState, Turn, TurnPlan, UserMsg } from "../lib/store";
import { phaseCounts } from "../lib/store";
import type { GateDecision } from "../lib/api";
import ToolCallCard from "./ToolCallCard";
import ApprovalCard from "./ApprovalCard";
import ReviewCard from "./ReviewCard";

const PHASE_ICON: Record<string, string> = {
  frame: "🔭",
  investigate: "🔎",
  act: "🛠️",
  verify: "✅",
  close: "📓",
};

interface Props {
  live: LiveState;
  busy: boolean;
  onDecide: (gateId: string, d: GateDecision, opts: { params?: Record<string, unknown>; reason?: string }) => void;
  onReview: (reviewId: string, d: GateDecision, opts: { text?: string }) => void;
  onSend: (text: string) => void;
}

type Item = { seq: number; kind: "turn"; turn: Turn } | { seq: number; kind: "msg"; msg: UserMsg };

// The primary interaction surface (UI-SPEC §2 / obs 2): a real two-way chat SESSION that is ALSO
// the COMPLETE, human-readable journal. Each agent turn renders, top to bottom, everything the
// journal holds for that phase — objective · plan · reasoning · tool calls · observations ·
// rejections · the write-gate — as a compact SUMMARY the owner can EXPAND for depth. The
// operator's own messages interleave by seq, and a composer lets the human steer or answer.
export default function ChatPane({ live, busy, onDecide, onReview, onSend }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [live.turns.length, live.turns.at(-1)?.calls.length, live.messages.length, live.gate?.gate_id, live.review?.review_id, live.state]);

  // interleave agent turns + operator messages by seq
  const items: Item[] = [
    ...live.turns.map((turn) => ({ seq: turn.key, kind: "turn" as const, turn })),
    ...live.messages.map((msg) => ({ seq: msg.seq + 0.5, kind: "msg" as const, msg })),
  ].sort((a, b) => a.seq - b.seq);

  // iteration badge: INVESTIGATE (and any looped phase) repeats — number each turn within its
  // phase so a looped phase reads "iteration 2 of 2" rather than a silent duplicate.
  const totals = phaseCounts(live);
  const iterByKey = new Map<number, number>();
  {
    const seen: Record<string, number> = {};
    for (const t of live.turns) {
      seen[t.phase] = (seen[t.phase] ?? 0) + 1;
      iterByKey.set(t.key, seen[t.phase]);
    }
  }

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  };

  const canChat = live.sessionId != null && live.state !== "closed";
  const suspended = live.state === "suspended" || live.state === "awaiting_review";

  return (
    <div className="chat">
      <div className="chat__header">
        <h2 className="pane-title">Investigation chat</h2>
        <p className="pane-subtitle">
          The complete journal — objective, plan, reasoning, tools and findings, per phase. Expand
          any step for detail; steer the agent any time.
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
            <TurnCard
              key={`t${it.turn.key}`}
              turn={it.turn}
              live={live}
              busy={busy}
              iteration={iterByKey.get(it.turn.key) ?? 1}
              looped={(totals[it.turn.phase] ?? 0) > 1}
              onDecide={onDecide}
              onReview={onReview}
            />
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

interface TurnCardProps {
  turn: Turn;
  live: LiveState;
  busy: boolean;
  iteration: number;
  looped: boolean;
  onDecide: (gateId: string, d: GateDecision, opts: { params?: Record<string, unknown>; reason?: string }) => void;
  onReview: (reviewId: string, d: GateDecision, opts: { text?: string }) => void;
}

// One agent turn = one phase, rendered as the COMPLETE journal entry: (a) phase header, (b) the
// objective, (c) the plan (collapsed), (d) the reasoning, (e) the tool-call cards, (f) the
// observations (collapsed), (g) any rejections inline, (h) the write-gate. Default view is
// compact — objective + reasoning with plan/tools/observations collapsed; the owner expands.
function TurnCard({ turn, live, busy, iteration, looped, onDecide, onReview }: TurnCardProps) {
  const gate = turn.gateId ? live.gates[turn.gateId] : undefined;
  const decision = turn.gateId ? live.decisions[turn.gateId] : undefined;
  const isOpenGate = gate && live.gate?.gate_id === gate.gate_id && !decision;
  // the phase-review (owner 2026-07-23) — parallels the write-gate, on its own turn slot
  const review = turn.reviewId ? live.reviews[turn.reviewId] : undefined;
  const reviewDecision = turn.reviewId ? live.reviewDecisions[turn.reviewId] : undefined;
  const isOpenReview = review && live.review?.review_id === review.review_id && !reviewDecision;

  return (
    <article className="turn">
      {/* (a) phase header + iteration badge */}
      <div className="turn__head">
        <span className="turn__icon" aria-hidden="true">
          {PHASE_ICON[turn.phase] ?? "•"}
        </span>
        <span className="turn__phase">{turn.phase}</span>
        {looped && (
          <span className="turn__iter" title={`${turn.phase} looped — this is iteration ${iteration}`}>
            iteration {iteration}
          </span>
        )}
      </div>

      {/* (b) objective — the phase goal, one concise line */}
      {turn.objective && <p className="turn__objective">{turn.objective}</p>}

      {/* (c) plan — collapsed: how it intends to use the tools + graph/hypothesis ops */}
      {turn.plan && <PlanDetails plan={turn.plan} />}

      {/* (d) reasoning — the narrative */}
      {turn.reasoning ? (
        <p className="turn__reasoning">{turn.reasoning}</p>
      ) : gate && !decision ? (
        <p className="turn__reasoning turn__reasoning--muted">Proposing an action…</p>
      ) : null}

      {/* (e) tool calls — the existing collapsible cards, unchanged */}
      {turn.calls.length > 0 && (
        <div className="turn__calls">
          {turn.calls.map((c) => (
            <ToolCallCard key={c.seq} call={c} />
          ))}
        </div>
      )}

      {/* (f) observations — collapsed: what the phase discovered + the beliefs it moved */}
      <ObservationsDetails turn={turn} live={live} />

      {/* (g) rejections — a small inline note, never a separate panel */}
      {turn.rejections.length > 0 && (
        <p className="turn__rejections" title="evidence the engine refused to fold (bounded repair)">
          <span className="turn__rejections-count">
            {turn.rejections.length} op{turn.rejections.length === 1 ? "" : "s"} dropped
          </span>{" "}
          {rejectionReasons(turn)}
        </p>
      )}

      {/* (h) the write-gate — ApprovalCard, unchanged */}
      {gate && (isOpenGate || decision) && (
        <ApprovalCard
          gate={gate}
          decision={decision}
          busy={busy}
          onDecide={(d, opts) => onDecide(gate.gate_id, d, opts)}
        />
      )}

      {/* (i) the phase-review — ReviewCard (the DIRECTION approval before advancing) */}
      {review && (isOpenReview || reviewDecision) && (
        <ReviewCard
          review={review}
          decision={reviewDecision}
          busy={busy}
          onDecide={(d, opts) => onReview(review.review_id, d, opts)}
        />
      )}
    </article>
  );
}

// (c) PLAN — collapsible <details>. SUMMARY: "planned N tool calls · M graph ops". EXPANDED: the
// tools available, the planned calls, and the planned graph/hypothesis ops.
function PlanDetails({ plan }: { plan: TurnPlan }) {
  const nCalls = plan.plannedCalls.length;
  const nOps = plan.plannedOps.length;
  if (nCalls === 0 && nOps === 0 && plan.available.length === 0) return null;
  return (
    <details className="turn__fold turn__plan">
      <summary className="turn__fold-summary">
        <span className="turn__fold-chevron" aria-hidden="true">▶</span>
        <span className="turn__fold-kind turn__fold-kind--plan">plan</span>
        <span className="turn__fold-label">
          planned {nCalls} tool call{nCalls === 1 ? "" : "s"} · {nOps} graph op{nOps === 1 ? "" : "s"}
        </span>
      </summary>
      <div className="turn__fold-body">
        {plan.available.length > 0 && (
          <PlanRow label="tools available" items={plan.available} className="turn__chip--available" />
        )}
        {nCalls > 0 && (
          <PlanRow label="planned calls" items={plan.plannedCalls} className="turn__chip--call" />
        )}
        {nOps > 0 && (
          <PlanRow label="planned graph/hypothesis ops" items={plan.plannedOps} className="turn__chip--op" />
        )}
      </div>
    </details>
  );
}

function PlanRow({ label, items, className }: { label: string; items: string[]; className: string }) {
  return (
    <div className="turn__plan-row">
      <span className="turn__plan-rowlabel">{label}</span>
      <span className="turn__chips">
        {items.map((it, i) => (
          <code key={`${it}-${i}`} className={`turn__chip ${className}`}>
            {it}
          </code>
        ))}
      </span>
    </div>
  );
}

// (f) OBSERVATIONS — collapsible <details>. SUMMARY: "discovered X nodes · Y facts · moved Z
// hypotheses". EXPANDED: the fact/node ids the phase grew + the hypothesis moves it made.
function ObservationsDetails({ turn, live }: { turn: Turn; live: LiveState }) {
  const facts = turn.obs.factIds.map((id) => live.facts[id]).filter(Boolean);
  const nNodes = turn.obs.nodeIds.length;
  const nFacts = turn.obs.factIds.length;
  const nEvents = turn.obs.eventIds.length;
  const moves = turn.obs.hypotheses;
  if (nNodes === 0 && nFacts === 0 && nEvents === 0 && moves.length === 0) return null;

  return (
    <details className="turn__fold turn__obs">
      <summary className="turn__fold-summary">
        <span className="turn__fold-chevron" aria-hidden="true">▶</span>
        <span className="turn__fold-kind turn__fold-kind--obs">observations</span>
        <span className="turn__fold-label">
          discovered {nNodes} node{nNodes === 1 ? "" : "s"} · {nFacts} fact{nFacts === 1 ? "" : "s"} ·
          moved {moves.length} hypothes{moves.length === 1 ? "is" : "es"}
        </span>
      </summary>
      <div className="turn__fold-body">
        {facts.length > 0 && (
          <ul className="turn__facts">
            {facts.map((f) => (
              <li
                key={f.id}
                className={[f.state === "superseded" ? "is-superseded" : "", f.provisional ? "is-provisional" : ""]
                  .join(" ")
                  .trim()}
              >
                <code>{shortSubject(f.subject)}</code> <strong>{f.predicate}</strong> = {formatValue(f.value)}
                {f.unit ? ` ${f.unit}` : ""}
                {f.source && <span className="turn__facts-src"> · {f.source}</span>}
                {f.provisional && <span className="prov-chip">provisional</span>}
              </li>
            ))}
          </ul>
        )}
        {turn.obs.nodeIds.length > 0 && (
          <p className="turn__obs-ids">
            <span className="turn__obs-idlabel">nodes</span>
            {turn.obs.nodeIds.map((id) => (
              <code key={id} className="turn__obs-id">{shortSubject(id)}</code>
            ))}
          </p>
        )}
        {moves.length > 0 && (
          <ul className="turn__beliefs">
            {moves.map((m, idx) => {
              const h = live.hypotheses[m.id];
              return (
                <li key={`${m.id}-${idx}`}>
                  <span className={`belief-chip belief-chip--${m.status ?? m.action}`}>{m.status ?? m.action}</span>{" "}
                  {h?.statement ?? m.id}
                  {m.basis && <span className="turn__beliefs-basis"> — {m.basis}</span>}
                </li>
              );
            })}
          </ul>
        )}
        {nEvents > 0 && (
          <p className="turn__obs-events">
            +{nEvents} event{nEvents === 1 ? "" : "s"} recorded
          </p>
        )}
      </div>
    </details>
  );
}

// distinct rejection reasons for the inline note ("2 ops dropped: unknown predicate; unknown node ref")
function rejectionReasons(turn: Turn): string {
  const reasons: string[] = [];
  for (const r of turn.rejections) if (!reasons.includes(r.reason)) reasons.push(r.reason);
  return reasons.join("; ");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function shortSubject(subject: string): string {
  const idx = subject.indexOf(":");
  const tail = idx >= 0 ? subject.slice(idx + 1) : subject;
  return tail.length > 20 ? `${tail.slice(0, 19)}…` : tail;
}
