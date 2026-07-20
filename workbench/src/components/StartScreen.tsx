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
  const sessionById = useMemo(() => {
    const m = new Map<string, SessionListItem>();
    for (const s of sessions) m.set(s.subject.id, s);
    return m;
  }, [sessions]);

  // the run status of a catalog incident, for the summary badge + colour
  function statusOf(id: string): { key: string; label: string } {
    const s = sessionById.get(id);
    if (!s) return { key: "waiting", label: "not started" };
    if (s.state === "suspended") return { key: "suspended", label: "awaiting approval" };
    if (s.state === "running") return { key: "running", label: "running" };
    if (s.state === "closed") return { key: s.outcome || "closed", label: s.outcome || "closed" };
    return { key: s.state, label: s.state };
  }
  const done = sessions.filter((s) => s.state === "closed").length;

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
          <div className="start__section-head">
            <h2 className="start__section-title">All incidents — every layer</h2>
            <span className="start__summary-count">{done}/{forDomain.length} completed</span>
          </div>
          <div className="start__cards">
            {forDomain.map((c) => {
              const st = statusOf(c.id);
              const ran = st.key !== "waiting";
              return (
                <button
                  key={c.id}
                  className={`incident-card incident-card--${st.key}`}
                  disabled={busy}
                  onClick={() =>
                    ran ? onOpenExisting(`${c.domain}:${c.id}`) : onStart({ domain: c.domain, id: c.id, kind: c.kind })
                  }
                >
                  <div className="incident-card__top">
                    <span className="incident-card__id">{c.id}</span>
                    <span className="incident-card__layer">{c.layer}</span>
                  </div>
                  <p className="incident-card__title">{c.title}</p>
                  <div className="incident-card__foot">
                    <span className={`run-status run-status--${st.key}`}>
                      <span className="run-status__dot" />
                      {st.label}
                    </span>
                    <span className="incident-card__cta">{ran ? "View →" : "Start →"}</span>
                  </div>
                </button>
              );
            })}
            {forDomain.length === 0 && !loadErr && <p className="start__empty">Loading incidents…</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
