import { useEffect, useState } from "react";
import type { LiveState, Selection } from "../lib/store";
import { activePhase, hypothesisList, phaseCounts } from "../lib/store";
import type { GateDecision } from "../lib/api";
import PhaseStepper from "./PhaseStepper";
import ChatPane from "./ChatPane";
import LiveGraph from "./LiveGraph";
import HypothesisPanel from "./HypothesisPanel";
import DiscoveryPanel from "./DiscoveryPanel";
import PostmortemCard from "./PostmortemCard";
import type { PanelControlState } from "./PanelControls";

/** The three main vertical panels — each with its own maximize/minimize controls. */
type PanelId = "chat" | "graph" | "hypotheses";

interface Props {
  live: LiveState;
  busy: boolean;
  error: string | null;
  title?: string; // the incident's one-line description (CatalogItem.title) — M2
  onDecide: (gateId: string, d: GateDecision, opts: { params?: Record<string, unknown>; reason?: string }) => void;
  onReview: (reviewId: string, d: GateDecision, opts: { text?: string }) => void;
  onSend: (text: string) => void;
  onBack: () => void;
}

export default function Workbench({
  live,
  busy,
  error,
  title,
  onDecide,
  onReview,
  onSend,
  onBack,
}: Props) {
  // one selection shared by the graph + the hypotheses (obs 8): clicking a fact/hypothesis
  // cross-highlights the node + fact in the graph, and clicking a node selects it here.
  const [selection, setSelection] = useState<Selection | null>(null);

  // The shared PANEL-LAYOUT model (generalizes the chat's original "Full window" toggle): each of
  // the three vertical panels — chat, graph, hypotheses — can MAXIMIZE (expand to the full workbench
  // width, hiding the others; at most one at a time) or MINIMIZE (collapse to a thin labeled strip;
  // independent per panel). Maximizing a panel clears its own minimized flag; minimizing the
  // maximized panel restores the split. Esc restores from any maximize (the chat's keyboard escape
  // hatch, now shared). Default state (no max, none min) renders the original split untouched.
  const [maximized, setMaximized] = useState<PanelId | null>(null);
  const [minimized, setMinimized] = useState<Record<PanelId, boolean>>({
    chat: false,
    graph: false,
    hypotheses: false,
  });

  const toggleMaximize = (id: PanelId) => {
    setMaximized((cur) => (cur === id ? null : id));
    setMinimized((m) => (m[id] ? { ...m, [id]: false } : m)); // maximize un-minimizes itself
  };
  const minimizePanel = (id: PanelId) => {
    setMinimized((m) => ({ ...m, [id]: true }));
    setMaximized((cur) => (cur === id ? null : cur)); // minimizing the maximized panel un-maximizes it
  };
  const restorePanel = (id: PanelId) => setMinimized((m) => ({ ...m, [id]: false }));

  useEffect(() => {
    if (!maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMaximized(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maximized]);

  // the control-cluster props each panel renders in its own header (chat/graph/hypotheses headers)
  const panelProps = (id: PanelId): PanelControlState => ({
    maximized: maximized === id,
    onToggleMaximize: () => toggleMaximize(id),
    onMinimize: () => minimizePanel(id),
  });

  // The blank-page fix: a NEW investigation spins up through `open()` (reset → createSession →
  // seed) / `openExisting()` (reset → getSnapshot → seed). Between the reset and the seed the store
  // is empty AND has no session id, so the panes below have nothing to render — a blank workbench
  // for however long the engine takes to run the incident to its first pause. Show an explicit
  // "processing…" state for that window instead. `!live.sessionId` fires ONLY during that gap: the
  // seed sets the id, so once any turn/node exists (or a later gate makes `busy` true again) the
  // full workbench renders normally.
  const starting = busy && !live.sessionId;
  if (starting) {
    return (
      <div className="workbench workbench--starting">
        <div className="starting" role="status" aria-live="polite">
          <div className="starting__spinner" aria-hidden="true" />
          <div className="starting__title">Processing…</div>
          <p className="starting__sub">
            Starting the investigation — the engine is framing the incident and running the
            root-cause loop. The graph, chat and hypotheses appear as soon as the first phase lands.
          </p>
          <button className="starting__back" onClick={onBack}>
            ← Incidents
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="workbench">
      <PhaseStepper
        subject={live.subject}
        rail={live.phaseRail}
        reached={live.phasesRun}
        current={activePhase(live)}
        counts={phaseCounts(live)}
        state={live.state}
        outcome={live.outcome}
        discoveredLayer={live.discoveredLayer}
        title={title}
        onBack={onBack}
      />
      {error && <div className="workbench__error">{error}</div>}
      {/* When a panel is maximized only it renders (full width); otherwise all three render, each
          either full or — if minimized — as a thin restorable strip. `shows(id)` gates a panel out
          entirely while another is maximized; `strip(id)` is true when a panel should collapse. */}
      <div className={`workbench__body${maximized ? " workbench__body--maxed" : ""}`}>
        {/* CHAT */}
        {(!maximized || maximized === "chat") &&
          (minimized.chat && !maximized ? (
            <MinimizedStrip label="Chat" onRestore={() => restorePanel("chat")} />
          ) : (
            <section className={`pane pane--chat${maximized === "chat" ? " pane--maxed" : ""}`}>
              <ChatPane
                live={live}
                busy={busy}
                onDecide={onDecide}
                onReview={onReview}
                onSend={onSend}
                panel={panelProps("chat")}
              />
            </section>
          ))}

        {/* GRAPH */}
        {(!maximized || maximized === "graph") &&
          (minimized.graph && !maximized ? (
            <MinimizedStrip label="Graph" onRestore={() => restorePanel("graph")} />
          ) : (
            <section className={`pane pane--graph${maximized === "graph" ? " pane--maxed" : ""}`}>
              <LiveGraph live={live} selection={selection} onSelect={setSelection} panel={panelProps("graph")} />
            </section>
          ))}

        {/* HYPOTHESES — the right side holds only what's about THIS investigation: the LIVE belief
            state. The chat IS the complete journal (so the Journal + Rejections panels are gone —
            rejections render inline in their turn), the incident SWITCHER is gone (navigation lives
            in the "← Incidents" back button; similar/related incidents surface in the graph), and
            Discovery appears only when it has something to show. */}
        {(!maximized || maximized === "hypotheses") &&
          (minimized.hypotheses && !maximized ? (
            <MinimizedStrip label="Hypotheses" onRestore={() => restorePanel("hypotheses")} />
          ) : (
            <aside className={`workbench__sidebar${maximized === "hypotheses" ? " pane--maxed" : ""}`}>
              {/* the close-out card (M29): once the investigation is CLOSED, lead the sidebar with the
                  postmortem the engine served in every bundle (root cause · ruled-out-with-basis ·
                  timeline · narrative) — previously computed and rendered by nothing. */}
              {live.state === "closed" && live.postmortem && (
                <section className="pane pane--postmortem">
                  <PostmortemCard postmortem={live.postmortem} outcome={live.outcome} />
                </section>
              )}
              <section className="pane pane--hypotheses">
                <HypothesisPanel
                  hypotheses={hypothesisList(live)}
                  facts={live.facts}
                  nodes={live.nodes}
                  selection={selection}
                  onSelect={setSelection}
                  panel={panelProps("hypotheses")}
                />
              </section>
              {(Object.keys(live.discovery.class_hints).length > 0 ||
                Object.keys(live.discovery.quarantined_names).length > 0) && (
                <section className="pane pane--discovery">
                  <DiscoveryPanel discovery={live.discovery} />
                </section>
              )}
            </aside>
          ))}
      </div>
    </div>
  );
}

/** A MINIMIZED panel — a thin labeled strip that restores the panel on click (the whole strip is
 *  the restore control, for a large keyboard/pointer target). */
function MinimizedStrip({ label, onRestore }: { label: string; onRestore: () => void }) {
  return (
    <button
      type="button"
      className="pane pane--min"
      onClick={onRestore}
      aria-label={`Restore the ${label} panel`}
      title={`Restore the ${label} panel`}
    >
      <span className="pane-min__icon" aria-hidden="true">⤢</span>
      <span className="pane-min__label">{label}</span>
    </button>
  );
}
