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

interface Props {
  live: LiveState;
  busy: boolean;
  error: string | null;
  layer?: string;
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
  layer,
  title,
  onDecide,
  onReview,
  onSend,
  onBack,
}: Props) {
  // one selection shared by the graph + the hypotheses (obs 8): clicking a fact/hypothesis
  // cross-highlights the node + fact in the graph, and clicking a node selects it here.
  const [selection, setSelection] = useState<Selection | null>(null);

  // full-window story mode (owner: "I should be able to see the chat in a full window"): the chat
  // pane expands to the whole workbench body — graph + sidebar hidden — for reading the complete
  // investigation end to end. Esc restores the split layout (accessible: keyboard escape hatch).
  const [chatExpanded, setChatExpanded] = useState(false);
  useEffect(() => {
    if (!chatExpanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setChatExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chatExpanded]);

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
        layer={layer}
        title={title}
        onBack={onBack}
      />
      {error && <div className="workbench__error">{error}</div>}
      <div className={`workbench__body${chatExpanded ? " workbench__body--chat-full" : ""}`}>
        <section className={`pane pane--chat${chatExpanded ? " pane--chat-full" : ""}`}>
          <ChatPane
            live={live}
            busy={busy}
            onDecide={onDecide}
            onReview={onReview}
            onSend={onSend}
            expanded={chatExpanded}
            onToggleExpand={() => setChatExpanded((v) => !v)}
          />
        </section>
        {/* graph + sidebar collapse away in full-window story mode — the chat is the whole view */}
        {!chatExpanded && (
        <section className="pane pane--graph">
          <LiveGraph live={live} selection={selection} onSelect={setSelection} />
        </section>
        )}
        {/* The right side holds only what's about THIS investigation: the LIVE belief state.
            The chat IS the complete journal (so the Journal + Rejections panels are gone —
            rejections render inline in their turn), the incident SWITCHER is gone (navigation
            lives in the "← Incidents" back button; similar/related incidents surface in the
            graph), and Discovery appears only when it has something to show. */}
        {!chatExpanded && (
        <aside className="workbench__sidebar">
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
            />
          </section>
          {(Object.keys(live.discovery.class_hints).length > 0 ||
            Object.keys(live.discovery.quarantined_names).length > 0) && (
            <section className="pane pane--discovery">
              <DiscoveryPanel discovery={live.discovery} />
            </section>
          )}
        </aside>
        )}
      </div>
    </div>
  );
}
