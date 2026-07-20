import { useState } from "react";
import type { GraphFact, LedgerItem } from "../types";
import type { LiveNode, Selection } from "../lib/store";

function shortId(id: string): string {
  const i = id.indexOf(":");
  return i >= 0 ? id.slice(i + 1) : id;
}

const STATUS_RANK: Record<string, number> = {
  confirmed: 0,
  supported: 1,
  proposed: 2,
  refuted: 3,
};

function rankOf(status: string): number {
  return STATUS_RANK[status] ?? 2.5;
}

function statusLabel(status: string): string {
  return status.length ? status.charAt(0).toUpperCase() + status.slice(1) : status;
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

interface Props {
  ledger: LedgerItem[];
  facts: Record<string, GraphFact>;
  nodes: Record<string, LiveNode>;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
}

/** One clickable evidence row (obs 8). The reasoner's supporting/refuting lists may hold a Fact
 *  id (→ predicate = value · source) OR a node id (the entity it points at); either way, clicking
 *  it highlights the relevant node in the graph. */
function FactRow({
  fid,
  facts,
  nodes,
  selected,
  onSelect,
}: {
  fid: string;
  facts: Record<string, GraphFact>;
  nodes: Record<string, LiveNode>;
  selected: boolean;
  onSelect: (sel: Selection) => void;
}) {
  const f = facts[fid];
  const n = !f ? nodes[fid] : undefined;
  return (
    <li>
      <button
        className={`evrow${selected ? " is-selected" : ""}`}
        onClick={() => onSelect({ kind: "fact", id: fid })}
        title={f ? `on ${f.subject}` : fid}
      >
        {f ? (
          <>
            <strong>{f.predicate}</strong> = {fmtValue(f.value)}
            {f.unit ? ` ${f.unit}` : ""}
            {f.source ? <span className="evrow__src"> · {f.source}</span> : null}
          </>
        ) : n ? (
          <>
            <span className="evrow__kind">{n.type}</span> <strong>{shortId(n.id)}</strong>
          </>
        ) : (
          <code className="evrow__id">{shortId(fid)}</code>
        )}
      </button>
    </li>
  );
}

// The Popperian ledger (obs 8): each hypothesis expands to the FACTS corroborating/refuting it and
// its CHAIN OF EVENTS. Every fact/link is clickable — it cross-highlights that node + fact in the
// graph (a shared selection lifted to the workbench), so "which facts back this?" is one click.
export default function HypothesisLedger({ ledger, facts, nodes, selection, onSelect }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);
  const sorted = [...ledger].sort((a, b) => {
    const rankDiff = rankOf(a.status) - rankOf(b.status);
    if (rankDiff !== 0) return rankDiff;
    return b.confidence - a.confidence;
  });

  return (
    <div className="ledger">
      <h2 className="pane-title">Hypothesis Ledger</h2>
      <p className="pane-subtitle">
        {sorted.length} hypothes{sorted.length === 1 ? "is" : "es"} considered — ranked, both sides
        shown. Expand for the evidence chain.
      </p>
      <div className="ledger-list">
        {sorted.map((item) => {
          const isRefuted = item.status === "refuted";
          const pct = Math.round(item.confidence * 100);
          const open = openId === item.id;
          const chain = item.chain ?? [];
          return (
            <article key={item.id} className={`ledger-card ledger-card--${item.status}`}>
              <button
                className="ledger-card__header ledger-card__toggle"
                onClick={() => setOpenId(open ? null : item.id)}
                aria-expanded={open}
              >
                <span className={`ledger-card__chevron ${open ? "is-open" : ""}`}>▶</span>
                <span className={`badge badge--${item.status}`}>{statusLabel(item.status)}</span>
                <span className="ledger-card__confidence-label">{pct}%</span>
              </button>
              <h3 className={`ledger-card__statement${isRefuted ? " is-struck" : ""}`}>
                {item.statement}
              </h3>
              <div className="confidence-bar">
                <div className="confidence-bar__fill" style={{ width: `${pct}%` }} />
              </div>
              {!open && (
                <div className="ledger-card__evidence">
                  <span className="evidence-chip evidence-chip--supporting">
                    + {item.supporting.length} supporting
                  </span>
                  <span className="evidence-chip evidence-chip--refuting">
                    − {item.refuting.length} refuting
                  </span>
                  {chain.length > 0 && (
                    <span className="evidence-chip evidence-chip--chain">⛓ {chain.length} chain</span>
                  )}
                </div>
              )}

              {open && (
                <div className="ledger-card__detail">
                  <p className="ledger-card__basis">{item.basis}</p>
                  {item.root_candidate && (
                    <p className="ledger-card__root">
                      Root candidate:{" "}
                      <button
                        className="ledger-card__rootlink"
                        onClick={() => onSelect({ kind: "node", id: item.root_candidate! })}
                      >
                        <code>{item.root_candidate}</code>
                      </button>
                    </p>
                  )}

                  {item.supporting.length > 0 && (
                    <div className="ev-group">
                      <span className="ev-group__label ev-group__label--supporting">Supporting facts</span>
                      <ul className="ev-list">
                        {item.supporting.map((fid) => (
                          <FactRow
                            key={fid}
                            fid={fid}
                            facts={facts}
                            nodes={nodes}
                            selected={selection?.kind === "fact" && selection.id === fid}
                            onSelect={onSelect}
                          />
                        ))}
                      </ul>
                    </div>
                  )}

                  {item.refuting.length > 0 && (
                    <div className="ev-group">
                      <span className="ev-group__label ev-group__label--refuting">Refuting facts</span>
                      <ul className="ev-list">
                        {item.refuting.map((fid) => (
                          <FactRow
                            key={fid}
                            fid={fid}
                            facts={facts}
                            nodes={nodes}
                            selected={selection?.kind === "fact" && selection.id === fid}
                            onSelect={onSelect}
                          />
                        ))}
                      </ul>
                    </div>
                  )}

                  {chain.length > 0 && (
                    <div className="ev-group">
                      <span className="ev-group__label ev-group__label--chain">Chain of events</span>
                      <ol className="chain-list">
                        {chain.map((c, i) => (
                          <li key={i}>
                            <button
                              className="chainrow"
                              onClick={() => onSelect({ kind: c.kind === "fact" ? "fact" : "node", id: c.ref })}
                              title={c.ref}
                            >
                              <span className={`chainrow__role chainrow__role--${c.role}`}>{c.role}</span>
                              <span className="chainrow__kind">{c.kind}</span>
                              {c.note ? <span className="chainrow__note">{c.note}</span> : <code>{c.ref}</code>}
                            </button>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}

                  {item.supporting.length === 0 && item.refuting.length === 0 && chain.length === 0 && (
                    <p className="ledger-card__empty">No evidence attached yet.</p>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
