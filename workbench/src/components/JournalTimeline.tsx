import type { JournalEntry } from "../types";

function refCount(refs: JournalEntry["refs"]): number {
  if (!refs) return 0; // non-phase kinds (invocation/plan/gate/…) carry no refs
  return (
    refs.nodes.length + refs.edges.length + refs.facts.length + refs.events.length + refs.hypotheses.length
  );
}

export default function JournalTimeline({ journal }: { journal: JournalEntry[] }) {
  const sorted = [...journal].sort((a, b) => a.seq - b.seq);

  return (
    <div className="journal">
      <h2 className="pane-title">Investigation Journal</h2>
      <p className="pane-subtitle">The running story, phase by phase.</p>
      <ol className="journal-list">
        {sorted.map((entry) => (
          <li key={entry.seq} className="journal-entry">
            <div className="journal-entry__marker">{entry.seq}</div>
            <div className="journal-entry__body">
              <div className="journal-entry__meta">
                <span className="phase-tag">{entry.phase}</span>
                <span className="journal-entry__actor">{entry.actor}</span>
              </div>
              <p className="journal-entry__narrative">{entry.narrative}</p>
              {entry.refs && refCount(entry.refs) > 0 && (
                <p className="journal-entry__refs">
                  touched {entry.refs.nodes.length} nodes, {entry.refs.edges.length} edges,{" "}
                  {entry.refs.facts.length} facts, {entry.refs.events.length} events
                  {entry.refs.hypotheses.length > 0
                    ? `, ${entry.refs.hypotheses.length} hypotheses`
                    : ""}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
