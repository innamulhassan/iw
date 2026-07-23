import { useState } from "react";
import type { LiveState, Selection } from "../lib/store";
import { activePhase, hypothesisList, phaseCounts } from "../lib/store";
import type { GateDecision } from "../lib/api";
import PhaseStepper from "./PhaseStepper";
import ChatPane from "./ChatPane";
import LiveGraph from "./LiveGraph";
import IncidentList from "./IncidentList";
import HypothesisPanel from "./HypothesisPanel";
import DiscoveryPanel from "./DiscoveryPanel";

interface Props {
  live: LiveState;
  busy: boolean;
  error: string | null;
  layer?: string;
  refreshKey: number;
  onDecide: (gateId: string, d: GateDecision, opts: { params?: Record<string, unknown>; reason?: string }) => void;
  onSend: (text: string) => void;
  onBack: () => void;
  onOpenExisting: (id: string) => void;
}

export default function Workbench({
  live,
  busy,
  error,
  layer,
  refreshKey,
  onDecide,
  onSend,
  onBack,
  onOpenExisting,
}: Props) {
  // one selection shared by the graph + the hypotheses (obs 8): clicking a fact/hypothesis
  // cross-highlights the node + fact in the graph, and clicking a node selects it here.
  const [selection, setSelection] = useState<Selection | null>(null);
  return (
    <div className="workbench">
      <PhaseStepper
        subject={live.subject}
        reached={live.phasesRun}
        current={activePhase(live)}
        counts={phaseCounts(live)}
        state={live.state}
        outcome={live.outcome}
        layer={layer}
        onBack={onBack}
      />
      {error && <div className="workbench__error">{error}</div>}
      <div className="workbench__body">
        <section className="pane pane--chat">
          <ChatPane live={live} busy={busy} onDecide={onDecide} onSend={onSend} />
        </section>
        <section className="pane pane--graph">
          <LiveGraph live={live} selection={selection} onSelect={setSelection} />
        </section>
        {/* The right side is now just the LIVE belief state + navigation — the chat IS the
            complete journal, so the redundant Journal + Rejections panels are gone (rejections
            render inline in their turn). Discovery appears only when it has something to show. */}
        <aside className="workbench__sidebar">
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
          <section className="pane pane--incidents">
            <IncidentList
              activeId={live.sessionId}
              refreshKey={refreshKey}
              stateKey={live.state}
              onOpen={onOpenExisting}
            />
          </section>
        </aside>
      </div>
    </div>
  );
}
