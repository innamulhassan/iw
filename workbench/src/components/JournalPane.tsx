import type { LiveState, Turn } from "../lib/store";
import ToolCallCard from "./ToolCallCard";

const PHASE_ICON: Record<string, string> = {
  frame: "🔭",
  triage: "🚑",
  hypothesize: "💡",
  investigate: "🔎",
  remediate: "🛠️",
  verify: "✅",
  close: "📓",
};

const DECISION_VERB: Record<string, string> = {
  approve: "Approved",
  refine: "Refined & approved",
  deny: "Denied",
};

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// UI-SPEC §3 + DEPTH pass: the journal keeps EVERYTHING per phase — the reasoning, each tool
// call (collapsible), the observations it produced (facts gathered + beliefs moved + nodes
// discovered), and the write-gate DECISION with WHO approved it. Grouped by phase, collapsible.
export default function JournalPane({ live }: { live: LiveState }) {
  const turns = live.turns;

  return (
    <div className="journal">
      <h2 className="pane-title">Journal</h2>
      <p className="pane-subtitle">Every step, phase by phase.</p>
      <div className="journal-list">
        {turns.length === 0 && <p className="journal__empty">The investigation hasn’t started yet.</p>}
        {turns.map((turn, i) => (
          <PhaseEntry key={turn.key} turn={turn} live={live} open={i === turns.length - 1} />
        ))}
      </div>
    </div>
  );
}

function PhaseEntry({ turn, live, open }: { turn: Turn; live: LiveState; open: boolean }) {
  const decision = turn.gateId ? live.decisions[turn.gateId] : undefined;
  const facts = turn.obs.factIds.map((id) => live.facts[id]).filter(Boolean);
  const nNodes = turn.obs.nodeIds.length;
  const nEvents = turn.obs.eventIds.length;

  return (
    <details className="journal-phase" open={open}>
      <summary className="journal-phase__summary">
        <span className="phase-tag">
          <span aria-hidden="true">{PHASE_ICON[turn.phase] ?? "•"}</span> {turn.phase}
        </span>
        <span className="journal-phase__counts">
          {turn.calls.length > 0 && <span>{turn.calls.length} calls</span>}
          {facts.length > 0 && <span>{facts.length} facts</span>}
          {turn.gateId && (
            <span className="journal-phase__gate">
              {decision ? DECISION_VERB[decision.decision] ?? decision.decision : "gate"}
            </span>
          )}
        </span>
      </summary>

      <div className="journal-phase__body">
        {/* 1. reasoning */}
        {turn.reasoning && (
          <div className="journal-step">
            <span className="journal-step__kind journal-step__kind--reason">reasoning</span>
            <p className="journal-step__text">{turn.reasoning}</p>
          </div>
        )}

        {/* 2. tool calls (collapsible) */}
        {turn.calls.length > 0 && (
          <div className="journal-step">
            <span className="journal-step__kind journal-step__kind--tool">tool calls</span>
            <div className="journal-step__calls">
              {turn.calls.map((c) => (
                <ToolCallCard key={c.seq} call={c} />
              ))}
            </div>
          </div>
        )}

        {/* 3. observations — facts gathered + beliefs moved + what was discovered */}
        {(facts.length > 0 || turn.obs.hypotheses.length > 0 || nNodes > 0 || nEvents > 0) && (
          <div className="journal-step">
            <span className="journal-step__kind journal-step__kind--obs">observations</span>
            {facts.length > 0 && (
              <ul className="journal-facts">
                {facts.map((f) => (
                  <li key={f.id} className={f.state === "superseded" ? "is-superseded" : ""}>
                    <code>{shortSubject(f.subject)}</code> <strong>{f.predicate}</strong> ={" "}
                    {formatValue(f.value)}
                    {f.unit ? ` ${f.unit}` : ""}
                    {f.source && <span className="journal-facts__src"> · {f.source}</span>}
                  </li>
                ))}
              </ul>
            )}
            {turn.obs.hypotheses.length > 0 && (
              <ul className="journal-beliefs">
                {turn.obs.hypotheses.map((m, idx) => {
                  const h = live.hypotheses[m.id];
                  return (
                    <li key={`${m.id}-${idx}`}>
                      <span className={`belief-chip belief-chip--${m.status ?? m.action}`}>
                        {m.status ?? m.action}
                      </span>{" "}
                      {h?.statement ?? m.id}
                      {m.basis && <span className="journal-beliefs__basis"> — {m.basis}</span>}
                    </li>
                  );
                })}
              </ul>
            )}
            {(nNodes > 0 || nEvents > 0) && (
              <p className="journal-discovered">
                grew the graph: {nNodes} node{nNodes === 1 ? "" : "s"} touched
                {nEvents > 0 ? `, ${nEvents} event${nEvents === 1 ? "" : "s"}` : ""}
              </p>
            )}
          </div>
        )}

        {/* 4. the write-gate decision — WHO approved it */}
        {turn.gateId && decision && (
          <div className="journal-step">
            <span className="journal-step__kind journal-step__kind--decision">decision</span>
            <div className="journal-decision">
              <span className={`decision-chip decision-chip--${decision.decision}`}>
                {DECISION_VERB[decision.decision] ?? decision.decision}
              </span>
              <span className="journal-decision__actor">
                by <strong>{decision.actor ?? "operator"}</strong>
                {decision.source ? ` · ${decision.source}` : ""}
              </span>
            </div>
            {decision.reason && <p className="journal-decision__reason">“{decision.reason}”</p>}
          </div>
        )}
      </div>
    </details>
  );
}

function shortSubject(subject: string): string {
  const idx = subject.indexOf(":");
  const tail = idx >= 0 ? subject.slice(idx + 1) : subject;
  return tail.length > 18 ? `${tail.slice(0, 17)}…` : tail;
}
