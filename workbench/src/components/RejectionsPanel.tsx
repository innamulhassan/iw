import type { RejectionItem } from "../types";

// Evidence WITHHELD this run (P3 step 2 — the bounded repair loop): every reducer rejection,
// derived from the journaled deltas, so the operator sees what the engine REFUSED to fold and
// why — the graph shows only admitted knowledge, and this panel shows the other side of that
// boundary. Replay-stable: a reopened investigation shows the same list.
export default function RejectionsPanel({ rejections }: { rejections: RejectionItem[] }) {
  if (rejections.length === 0) return null;

  return (
    <div className="rejections">
      <h2 className="pane-title">Ops dropped</h2>
      <p className="pane-subtitle">
        {rejections.length} op{rejections.length === 1 ? "" : "s"} the engine refused to fold —
        evidence withheld, with the reason recorded.
      </p>
      <ul className="rejections__list">
        {rejections.map((r) => (
          <li key={`${r.seq}:${r.op_index}`} className="rejections__row">
            <span className="rejections__phase">{r.phase ?? "—"}</span>
            <code className="rejections__op">{r.op_kind}</code>
            <span className="rejections__reason">{r.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
