import type { Postmortem } from "../types";

// The close-out projection (M29): the engine folds a postmortem from the journal + hypothesis
// store on EVERY bundle (never authored separately, so it cannot drift from what happened) — root
// cause, the ruled-out rivals WITH their basis, the surviving contributing factors, a structured
// event timeline, and the per-phase narrative. It was served in every bundle and rendered by
// nothing; this card surfaces it once the investigation closes. The ruled-out-with-basis and the
// structured timeline are unique to this projection — not recoverable from the hypotheses panel.
export default function PostmortemCard({ postmortem, outcome }: { postmortem: Postmortem; outcome: string }) {
  const { root_cause, ruled_out, contributing, timeline, narrative } = postmortem;
  return (
    <div className="postmortem">
      <div className="postmortem__head">
        <h2 className="pane-title">Post-incident review</h2>
        <span className={`postmortem__outcome postmortem__outcome--${outcome}`}>{outcome}</span>
      </div>
      <p className="pane-subtitle">
        The close-out, folded from the journal — the confirmed root cause, the rivals ruled out and
        why, the timeline, and the per-phase story.
      </p>

      {/* root cause — or an honest "no confirmed root" on a mitigated close */}
      <section className="postmortem__section">
        <span className="postmortem__label">Root cause</span>
        {root_cause ? (
          <div className="postmortem__root">
            <p className="postmortem__root-statement">{root_cause.statement}</p>
            <div className="postmortem__root-meta">
              {root_cause.root_candidate && (
                <code className="postmortem__root-node">{shortId(root_cause.root_candidate)}</code>
              )}
              <span className="postmortem__conf" title="engine-earned weighted evidence score">
                {pct(root_cause.confidence)} confidence
              </span>
            </div>
          </div>
        ) : (
          <p className="postmortem__none">
            No confirmed root cause — impact was mitigated but the cause was not independently
            confirmed at close.
          </p>
        )}
      </section>

      {/* ruled-out rivals WITH basis — the differential-diagnosis record */}
      {ruled_out.length > 0 && (
        <section className="postmortem__section">
          <span className="postmortem__label">Ruled out ({ruled_out.length})</span>
          <ul className="postmortem__ruledout">
            {ruled_out.map((r, i) => (
              <li key={i} className="postmortem__ruledout-item">
                <span className="postmortem__ruledout-stmt">{r.statement}</span>
                {r.basis && <span className="postmortem__ruledout-basis"> — {r.basis}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* surviving contributing factors (alive-but-not-confirmed hypotheses) */}
      {contributing.length > 0 && (
        <section className="postmortem__section">
          <span className="postmortem__label">Contributing factors ({contributing.length})</span>
          <ul className="postmortem__contributing">
            {contributing.map((c, i) => (
              <li key={i}>
                {c.statement}
                <span className="postmortem__conf"> · {pct(c.confidence)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* the structured event timeline — collapsible */}
      {timeline.length > 0 && (
        <details className="postmortem__fold">
          <summary className="postmortem__fold-summary">
            <span className="postmortem__fold-chevron" aria-hidden="true">▶</span>
            Timeline ({timeline.length} event{timeline.length === 1 ? "" : "s"})
          </summary>
          <ol className="postmortem__timeline">
            {timeline.map((t, i) => (
              <li key={i} className="postmortem__timeline-item">
                <span className="postmortem__timeline-at">{shortTime(t.at)}</span>
                <span className="postmortem__timeline-type">{t.type.replace(/_/g, " ")}</span>
                <code className="postmortem__timeline-entity">{shortId(t.entity)}</code>
              </li>
            ))}
          </ol>
        </details>
      )}

      {/* the per-phase narrative — collapsible */}
      {narrative.length > 0 && (
        <details className="postmortem__fold">
          <summary className="postmortem__fold-summary">
            <span className="postmortem__fold-chevron" aria-hidden="true">▶</span>
            Narrative ({narrative.length} phase{narrative.length === 1 ? "" : "s"})
          </summary>
          <ol className="postmortem__narrative">
            {narrative.map((n, i) => (
              <li key={i} className="postmortem__narrative-item">
                <span className="postmortem__narrative-phase">{n.phase}</span>
                <span className="postmortem__narrative-text">{n.text}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

// tail of a node id (`change_event:chg-db-500` → `chg-db-500`), truncated for the narrow card
function shortId(id: string): string {
  const idx = id.indexOf(":");
  const tail = idx >= 0 ? id.slice(idx + 1) : id;
  return tail.length > 28 ? `${tail.slice(0, 27)}…` : tail;
}

// ISO timestamp → HH:MM (the timeline is same-incident, so the date is noise)
function shortTime(at: string): string {
  const m = at.match(/T(\d{2}:\d{2})/);
  return m ? m[1] : at;
}
