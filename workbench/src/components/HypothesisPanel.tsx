import { useState } from "react";
import type { GraphFact, HypothesisItem } from "../types";
import type { LiveNode, Selection } from "../lib/store";
import { humanizePredicate } from "../lib/format";

function shortId(id: string): string {
  const i = id.indexOf(":");
  return i >= 0 ? id.slice(i + 1) : id;
}

function statusLabel(status: string): string {
  return status.length ? status.charAt(0).toUpperCase() + status.slice(1) : status;
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** The hypothesis's belief timestamp (engine-served, golden-deterministic): a readable HH:MM for
 *  the "updated HH:MM" line. Null when absent (a purely-live hypothesis before the bundle reconcile)
 *  or unparseable — the caller hides the line gracefully. */
function fmtStamp(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

interface Props {
  hypotheses: HypothesisItem[];
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
  kind,
  facts,
  nodes,
  selected,
  onSelect,
}: {
  fid: string;
  kind: "supporting" | "refuting";
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
        className={`evrow evrow--${kind}${selected ? " is-selected" : ""}${f?.provisional ? " is-provisional" : ""}`}
        onClick={() => onSelect({ kind: "fact", id: fid })}
        title={f ? `on ${f.subject}` : fid}
      >
        {/* corroborating vs disconfirming, made visible per-row (not just on the group label) */}
        <span className={`evrow__stance evrow__stance--${kind}`}>
          {kind === "refuting" ? "refutes" : "supports"}
        </span>
        {f ? (
          <>
            <strong>{humanizePredicate(f.predicate)}</strong> = {fmtValue(f.value)}
            {f.unit ? ` ${f.unit}` : ""}
            {f.source ? <span className="evrow__src"> · {f.source}</span> : null}
            {f.provisional && <span className="prov-chip">provisional</span>}
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

// The Popperian hypotheses (obs 8): each hypothesis expands to the FACTS corroborating/refuting it and
// its CHAIN OF EVENTS. Every fact/link is clickable — it cross-highlights that node + fact in the
// graph (a shared selection lifted to the workbench), so "which facts back this?" is one click.
// Rendered in the ENGINE's ranked() order exactly as given — the store carries the bundle order and
// this panel never re-sorts (the audit's divergent client-side ranking is gone). The % shown is the
// ENGINE-EARNED weighted evidence score (P4); the LLM's band survives only inside the basis text.
export default function HypothesisPanel({ hypotheses, facts, nodes, selection, onSelect }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div className="hypotheses">
      <h2 className="pane-title">Hypotheses</h2>
      <p className="pane-subtitle">
        {hypotheses.length} hypothes{hypotheses.length === 1 ? "is" : "es"} considered — engine-ranked,
        both sides shown. Expand for the evidence chain.
      </p>
      <div className="hypothesis-list">
        {hypotheses.map((item) => {
          const isRefuted = item.status === "refuted";
          const pct = Math.round(item.confidence * 100);
          const open = openId === item.id;
          const chain = item.chain ?? [];
          return (
            <article key={item.id} className={`hypothesis-card hypothesis-card--${item.status}`}>
              <button
                className="hypothesis-card__header hypothesis-card__toggle"
                onClick={() => setOpenId(open ? null : item.id)}
                aria-expanded={open}
              >
                <span className={`hypothesis-card__chevron ${open ? "is-open" : ""}`}>▶</span>
                <span className={`badge badge--${item.status}`}>{statusLabel(item.status)}</span>
                <span
                  className="hypothesis-card__score"
                  title="Engine-earned weighted evidence score (P4) — not the model's self-reported band"
                >
                  {pct}
                  <span className="hypothesis-card__score-unit">%</span>
                  <span className="hypothesis-card__score-tag">earned</span>
                </span>
              </button>
              <h3 className={`hypothesis-card__statement${isRefuted ? " is-struck" : ""}`}>
                {item.statement}
              </h3>
              <div className="confidence-bar">
                <div className="confidence-bar__fill" style={{ width: `${pct}%` }} />
              </div>
              {!open && (
                <div className="hypothesis-card__evidence">
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
                <div className="hypothesis-card__detail">
                  {/* WHEN the belief last moved (engine-served, deterministic) — falls back to when
                      it was first proposed; hidden entirely if the engine served neither. */}
                  {(() => {
                    const stamp = fmtStamp(item.updated_at ?? item.proposed_at);
                    return stamp ? <p className="hypothesis-card__stamp">updated {stamp}</p> : null;
                  })()}
                  {/* the basis — the reasoning / semantic-meta the belief rests on */}
                  <p className="hypothesis-card__basis">{item.basis}</p>
                  {item.root_candidate && (
                    <p className="hypothesis-card__root">
                      {/* a clickable chip cross-highlighting the root node in the graph (same onSelect
                          pattern the evidence rows use) */}
                      <button
                        className="hypothesis-card__rootlink"
                        onClick={() => onSelect({ kind: "node", id: item.root_candidate! })}
                        title={`highlight ${item.root_candidate} in the graph`}
                      >
                        root → <code>{item.root_candidate}</code>
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
                            kind="supporting"
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
                            kind="refuting"
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
                    <p className="hypothesis-card__empty">No evidence attached yet.</p>
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
