import type { PhaseRailItem, Subject } from "../types";

// M22: the phase rail is DATA, not a hardcoded list. The engine serves `phase_rail` (every declared
// phase + a `focus` flag derived from the playbook's writes_allowed role binding: the pre-action
// diagnostic phases are in-focus, later ones greyed until reached). A new playbook's phases render
// with no UI edit. Fallback: before the first snapshot lands, show the reached phases (all focus).

const OUTCOME_LABEL: Record<string, string> = {
  resolved: "Resolved",
  mitigated: "Mitigated",
  open: "Open",
};

interface Props {
  subject: Subject | null;
  /** The served declared phase rail (M22) — the stepper renders THIS, never a hardcoded list. */
  rail: PhaseRailItem[];
  reached: string[];
  current: string | null;
  state: string | null;
  outcome: string;
  /** How many turns each phase has run (investigate is ONE loop — repeats collapse onto its
   *  single step and surface as an ×N badge, never as extra columns). */
  counts?: Record<string, number>;
  /** The DISCOVERED fault layer — `null` UNTIL the engine confirms a root, then the earned layer
   *  name. The header shows "Layer — determining…" while null and never pre-reveals the catalog's
   *  pre-assigned guess (discovered, not assumed). */
  discoveredLayer?: string | null;
  title?: string; // the incident's one-line description (CatalogItem.title) — M2
  onBack: () => void;
}

export default function PhaseStepper({ subject, rail, reached, current, state, outcome, counts, discoveredLayer, title, onBack }: Props) {
  const reachedSet = new Set(reached);
  // the served rail drives the stepper; before the first snapshot, fall back to the reached phases
  const steps: PhaseRailItem[] = rail.length ? rail : reached.map((id) => ({ id, focus: true }));

  return (
    <header className="phase-bar">
      <div className="phase-bar__top">
        <div className="phase-bar__identity">
          <button className="btn btn--ghost phase-bar__back" onClick={onBack} title="Back to start">
            ← Incidents
          </button>
          <span className="phase-bar__kind">{subject?.kind ?? "incident"}</span>
          <span className="phase-bar__id">{subject?.id ?? "—"}</span>
          {title && (
            <span className="phase-bar__title" title={title}>
              {title}
            </span>
          )}
          {/* LAYER is DISCOVERED, not assumed: muted "determining…" while the engine hasn't confirmed
              a root, resolving to the earned layer once it has — never the catalog's up-front guess. */}
          {discoveredLayer ? (
            <span className="phase-bar__layer" title="Fault layer — discovered from the confirmed root cause">
              {discoveredLayer}
            </span>
          ) : (
            <span
              className="phase-bar__layer phase-bar__layer--determining"
              title="The fault layer is earned from the confirmed root cause — not assumed up front"
            >
              Layer — determining…
            </span>
          )}
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
        {steps.map(({ id: phase, focus }, i) => {
          const isReached = reachedSet.has(phase);
          const isCurrent = phase === current;
          // focus phases stay lit; later phases stay greyed UNTIL the run reaches them (data-driven
          // — `focus` comes from the served rail, not a UI-hardcoded ACTIVE set).
          const greyed = !focus && !isReached;
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
