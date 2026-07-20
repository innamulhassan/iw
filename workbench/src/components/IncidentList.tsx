import { useEffect, useState } from "react";
import type { SessionListItem } from "../types";
import { listSessions } from "../lib/api";

interface Props {
  activeId: string | null;
  refreshKey: number; // bump to re-fetch (e.g. after opening a new session)
  stateKey?: string | null; // the live session's state — re-fetch when it changes (e.g. → closed)
  onOpen: (id: string) => void;
}

// UI-SPEC §1: the incident list — every investigation, INCLUDING closed ones — openable to view
// its chat / graph / journal. Fed by GET /sessions.
export default function IncidentList({ activeId, refreshKey, stateKey, onOpen }: Props) {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);

  useEffect(() => {
    let alive = true;
    listSessions()
      .then((s) => alive && setSessions(s))
      .catch(() => alive && setSessions([]));
    return () => {
      alive = false;
    };
  }, [refreshKey, activeId, stateKey]);

  return (
    <div className="incident-list">
      <h2 className="pane-title">Incidents</h2>
      <p className="pane-subtitle">{sessions.length} investigation{sessions.length === 1 ? "" : "s"}</p>
      <ul className="incident-list__items">
        {sessions.length === 0 && <li className="incident-list__empty">No other investigations yet.</li>}
        {sessions.map((s) => (
          <li key={s.id}>
            <button
              className={`incident-row ${s.id === activeId ? "is-active" : ""}`}
              onClick={() => onOpen(s.id)}
            >
              <span className="incident-row__id">{s.subject.id}</span>
              <span className="incident-row__meta">
                <span className={`state-dot state-dot--${s.state}`} />
                <span className="incident-row__state">{s.state}</span>
                {s.outcome !== "open" && <span className={`outcome-pill outcome-pill--${s.outcome}`}>{s.outcome}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
