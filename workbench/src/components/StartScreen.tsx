import { useEffect, useMemo, useState } from "react";
import type { CatalogItem, SessionListItem, Subject } from "../types";
import { getCatalog, listSessions } from "../lib/api";

interface Props {
  onStart: (subject: Subject) => void;
  onOpenExisting: (id: string) => void;
  error: string | null;
  busy: boolean;
}

// UI-SPEC §1 — the start screen: a domain selector + an incident picker/number input to START
// an investigation (the catalog covers every layer), plus the list of other incidents (incl.
// CLOSED) to reopen.
export default function StartScreen({ onStart, onOpenExisting, error, busy }: Props) {
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [domain, setDomain] = useState<string>("app-incident");
  const [customId, setCustomId] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    getCatalog()
      .then((c) => {
        setCatalog(c);
        if (c[0]) setDomain(c[0].domain);
      })
      .catch((e) => setLoadErr(e instanceof Error ? e.message : String(e)));
    listSessions()
      .then(setSessions)
      .catch(() => setSessions([]));
  }, []);

  const domains = useMemo(() => Array.from(new Set(catalog.map((c) => c.domain))), [catalog]);
  const forDomain = catalog.filter((c) => c.domain === domain);

  return (
    <div className="start">
      <div className="start__inner">
        <header className="start__hero">
          <h1 className="start__title">Investigation Workbench</h1>
          <p className="start__tagline">
            Pick an incident and drive a governed, human-in-the-loop root-cause investigation.
          </p>
        </header>

        {(error || loadErr) && <div className="start__error">{error ?? loadErr}</div>}

        <section className="start__panel">
          <div className="start__row">
            <label className="start__field">
              <span className="start__label">Domain</span>
              <select value={domain} onChange={(e) => setDomain(e.target.value)} className="start__select">
                {(domains.length ? domains : ["app-incident"]).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <form
              className="start__field start__field--grow"
              onSubmit={(e) => {
                e.preventDefault();
                if (customId.trim()) onStart({ domain, id: customId.trim(), kind: "incident" });
              }}
            >
              <span className="start__label">Incident number</span>
              <div className="start__id-row">
                <input
                  className="start__input"
                  placeholder="e.g. INC-4821"
                  value={customId}
                  onChange={(e) => setCustomId(e.target.value)}
                />
                <button className="btn btn--primary" disabled={busy || !customId.trim()} type="submit">
                  Start →
                </button>
              </div>
            </form>
          </div>
        </section>

        <section className="start__catalog">
          <h2 className="start__section-title">Runnable incidents — every layer</h2>
          <div className="start__cards">
            {forDomain.map((c) => (
              <button
                key={c.id}
                className="incident-card"
                disabled={busy}
                onClick={() => onStart({ domain: c.domain, id: c.id, kind: c.kind })}
              >
                <div className="incident-card__top">
                  <span className="incident-card__id">{c.id}</span>
                  <span className="incident-card__layer">{c.layer}</span>
                </div>
                <p className="incident-card__title">{c.title}</p>
                <span className="incident-card__cta">Start investigation →</span>
              </button>
            ))}
            {forDomain.length === 0 && !loadErr && <p className="start__empty">Loading incidents…</p>}
          </div>
        </section>

        {sessions.length > 0 && (
          <section className="start__history">
            <h2 className="start__section-title">Other investigations</h2>
            <ul className="start__history-list">
              {sessions.map((s) => (
                <li key={s.id}>
                  <button className="incident-row" onClick={() => onOpenExisting(s.id)} disabled={busy}>
                    <span className="incident-row__id">{s.subject.id}</span>
                    <span className="incident-row__meta">
                      <span className={`state-dot state-dot--${s.state}`} />
                      <span className="incident-row__state">{s.state}</span>
                      {s.outcome !== "open" && (
                        <span className={`outcome-pill outcome-pill--${s.outcome}`}>{s.outcome}</span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
