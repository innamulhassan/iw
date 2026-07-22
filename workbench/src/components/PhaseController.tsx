import type { Outcome, Phase, Subject } from "../types";

// Canonical phase order the stepper always renders, regardless of which
// phases a given run actually reached (P7 5-phase algebra — incident.yaml).
// A run's phase list may repeat "investigate" (it is ONE loop): repeats
// collapse onto the single investigate step and surface as an ×N badge.
const ALL_PHASES: Phase[] = [
  "frame",
  "investigate",
  "act",
  "verify",
  "close",
];

const OUTCOME_LABEL: Record<string, string> = {
  resolved: "Resolved",
  mitigated: "Mitigated",
  open: "Open",
};

interface Props {
  phases: string[];
  outcome: Outcome;
  subject: Subject;
  rootCauseStatement?: string;
}

export default function PhaseController({ phases, outcome, subject, rootCauseStatement }: Props) {
  const reached = new Set(phases);
  const current = phases[phases.length - 1];
  // investigate is ONE loop — count how many times each phase ran (×N badge when > 1)
  const counts = new Map<string, number>();
  for (const p of phases) counts.set(p, (counts.get(p) ?? 0) + 1);

  return (
    <header className="phase-bar">
      <div className="phase-bar__top">
        <div className="phase-bar__identity">
          <span className="phase-bar__kind">{subject.kind}</span>
          <span className="phase-bar__id">{subject.id}</span>
          <span className={`outcome-badge outcome-badge--${outcome}`}>
            {OUTCOME_LABEL[outcome] ?? outcome}
          </span>
        </div>

        {rootCauseStatement && (
          <div className="phase-bar__root-cause">
            <span className="phase-bar__root-cause-label">Confirmed root cause</span>
            <span className="phase-bar__root-cause-text">{rootCauseStatement}</span>
          </div>
        )}
      </div>

      <ol className="phase-stepper">
        {ALL_PHASES.map((phase, i) => {
          const isReached = reached.has(phase);
          const isCurrent = phase === current;
          return (
            <li
              key={phase}
              className={[
                "phase-step",
                isReached ? "phase-step--reached" : "phase-step--pending",
                isCurrent ? "phase-step--current" : "",
              ]
                .join(" ")
                .trim()}
            >
              <span className="phase-step__index">{i + 1}</span>
              <span className="phase-step__label">{phase}</span>
              {(counts.get(phase) ?? 0) > 1 && (
                <span className="phase-step__count" title={`${phase} looped ${counts.get(phase)} times`}>
                  ×{counts.get(phase)}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </header>
  );
}
