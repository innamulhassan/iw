// Left pane — the incidents: the subject in focus + operator-linked related/similar (never auto).
import type { IncidentListItem } from '../model'

export function IncidentsPane({ incidents }: { incidents: IncidentListItem[] }) {
  return (
    <aside className="pane incidents" data-testid="incidents-pane">
      <h2>Incidents</h2>
      <ul>
        {incidents.map((i) => (
          <li key={i.id} className={`inc ${i.relation ?? ''}`}>
            <span className={`sev sev-${i.severity}`}>{i.severity}</span>
            <div className="inc-main">
              <div className="inc-id">{i.id}{i.relation === 'subject' && <em> · in focus</em>}</div>
              <div className="inc-title">{i.title}</div>
            </div>
            {i.relation && i.relation !== 'subject' && <span className={`rel ${i.relation}`}>{i.relation}</span>}
          </li>
        ))}
      </ul>
      <p className="hint">Related incidents are surfaced; linking is operator-controlled — no auto-merge.</p>
    </aside>
  )
}
