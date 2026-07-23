import type { Subject } from "../types";

// UI-SPEC §5 / P7 5-phase algebra (incident.yaml is the source of truth): FRAME · INVESTIGATE
// are the in-focus phases (triage folded into the act-entry decision, hypothesize into the ONE
// investigate loop); ACT · VERIFY · CLOSE are greyed until the run reaches them (the write-gate
// lives in ACT — every production-changing action, mitigation or remediation alike).
const ALL_PHASES = ["frame", "investigate", "act", "verify", "close"] as const;
const ACTIVE = new Set(["frame", "investigate"]);

const OUTCOME_LABEL: Record<string, string> = {
  resolved: "Resolved",
  mitigated: "Mitigated",
  open: "Open",
};

interface Props {
  subject: Subject | null;
  reached: string[];
  current: string | null;
  state: string | null;
  outcome: string;
  /** How many turns each phase has run (investigate is ONE loop — repeats collapse onto its
   *  single step and surface as an ×N badge, never as extra columns). */
  counts?: Record<string, number>;
  layer?: string;
  onBack: () => void;
}

export default function PhaseStepper({ subject, reached, current, state, outcome, counts, layer, onBack }: Props) {
  const reachedSet = new Set(reached);

  return (
    <header className="phase-bar">
      <div className="phase-bar__top">
        <div className="phase-bar__identity">
          <button className="btn btn--ghost phase-bar__back" onClick={onBack} title="Back to start">
            ← Incidents
          </button>
          <span className="phase-bar__kind">{subject?.kind ?? "incident"}</span>
          <span className="phase-bar__id">{subject?.id ?? "—"}</span>
          {layer && <span className="phase-bar__layer">{layer}</span>}
        </div>
        <div className="phase-bar__status">
          {state && (
            <span className={`state-badge state-badge--${state}`}>
              {state === "suspended"
                ? "awaiting approval"
                : state === "awaiting_review"
                  ? "awaiting review"
                  : state}
            </span>
          )}
          <span className={`outcome-badge outcome-badge--${outcome}`}>{OUTCOME_LABEL[outcome] ?? outcome}</span>
        </div>
      </div>

      <ol className="phase-stepper">
        {ALL_PHASES.map((phase, i) => {
          const isFocus = ACTIVE.has(phase);
          const isReached = reachedSet.has(phase);
          const isCurrent = phase === current;
          // owner focus is FRAME→INVESTIGATE: later phases stay greyed UNTIL the run reaches
          // them, then they light up (reached).
          const greyed = !isFocus && !isReached;
          return (
            <li
              key={phase}
              className={[
                "phase-step",
                greyed ? "phase-step--greyed" : "phase-step--infocus",
                isReached ? "phase-step--reached" : "phase-step--pending",
                isCurrent ? "phase-step--current" : "",
              ]
                .join(" ")
                .trim()}
              aria-disabled={greyed}
              title={greyed ? `${phase} (greyed until reached)` : phase}
            >
              <span className="phase-step__index">{i + 1}</span>
              <span className="phase-step__label">{phase}</span>
              {(counts?.[phase] ?? 0) > 1 && (
                <span className="phase-step__count" title={`${phase} looped ${counts?.[phase]} times`}>
                  ×{counts?.[phase]}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </header>
  );
}
