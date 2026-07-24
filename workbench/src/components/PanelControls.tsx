// The shared MAXIMIZE / MINIMIZE control cluster for the three workbench panels (chat · graph ·
// hypotheses). MAXIMIZE expands the panel to the full workbench width, hiding the others (at most
// one panel maximized at a time); MINIMIZE collapses it to a thin labeled strip that can be
// restored (independent per panel). The layout STATE lives in Workbench (the single owner) — each
// panel only renders this cluster in its own header and calls back. Both are real <button>s with
// aria-label + aria-pressed so the layout is keyboard- and screen-reader-navigable. This generalizes
// the chat's original bespoke "Full window" toggle into one control every panel shares.

/** The layout callbacks + state a panel needs to render its controls — supplied by Workbench. */
export interface PanelControlState {
  /** true when THIS panel is the one maximized to full width (its control reads "restore"). */
  maximized: boolean;
  onToggleMaximize: () => void;
  onMinimize: () => void;
}

export default function PanelControls({
  label,
  maximized,
  onToggleMaximize,
  onMinimize,
}: PanelControlState & { label: string }) {
  return (
    <div className="panel-ctls" role="group" aria-label={`${label} panel layout`}>
      <button
        type="button"
        className="panel-ctl"
        onClick={onMinimize}
        aria-label={`Minimize the ${label} panel`}
        title={`Minimize the ${label} panel to a strip`}
      >
        <span aria-hidden="true">–</span>
      </button>
      <button
        type="button"
        className="panel-ctl panel-ctl--max"
        onClick={onToggleMaximize}
        aria-pressed={maximized}
        aria-label={maximized ? `Restore the ${label} panel` : `Maximize the ${label} panel`}
        title={maximized ? "Restore the split layout (Esc)" : `Maximize the ${label} panel to full width`}
      >
        <span aria-hidden="true">⤢</span>
      </button>
    </div>
  );
}
