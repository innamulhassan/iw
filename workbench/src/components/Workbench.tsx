import { useState } from "react";
import type { LiveState, Selection } from "../lib/store";
import { activePhase, ledgerList } from "../lib/store";
import type { GateDecision } from "../lib/api";
import PhaseStepper from "./PhaseStepper";
import ChatPane from "./ChatPane";
import LiveGraph from "./LiveGraph";
import JournalPane from "./JournalPane";
import IncidentList from "./IncidentList";
import HypothesisLedger from "./HypothesisLedger";

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
  // one selection shared by the graph + the ledger (obs 8): clicking a fact/hypothesis
  // cross-highlights the node + fact in the graph, and clicking a node selects it here.
  const [selection, setSelection] = useState<Selection | null>(null);
  return (
    <div className="workbench">
      <PhaseStepper
        subject={live.subject}
        reached={live.phasesRun}
        current={activePhase(live)}
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
        <aside className="workbench__sidebar">
          <section className="pane pane--ledger">
            <HypothesisLedger
              ledger={ledgerList(live)}
              facts={live.facts}
              nodes={live.nodes}
              selection={selection}
              onSelect={setSelection}
            />
          </section>
          <section className="pane pane--journal">
            <JournalPane live={live} />
          </section>
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
