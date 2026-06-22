// Right pane — Phases & Steps ⇄ Graph (the two views of where the investigation is).
import { useState } from 'react'
import type { GraphSlice, PhaseSummary } from '../model'
import { GraphView } from './GraphView'

export function RightPane({ phases, graph }: { phases: PhaseSummary[]; graph: GraphSlice }) {
  const [tab, setTab] = useState<'phases' | 'graph'>('phases')
  return (
    <aside className="pane right" data-testid="right-pane">
      <div className="tabs" role="tablist">
        <button role="tab" className={tab === 'phases' ? 'active' : ''} onClick={() => setTab('phases')}>
          Phases &amp; Steps
        </button>
        <button role="tab" className={tab === 'graph' ? 'active' : ''} onClick={() => setTab('graph')}>
          Graph
        </button>
      </div>
      {tab === 'phases' ? (
        <ol className="phases" data-testid="phases-list">
          {phases.map((p) => (
            <li key={p.id} className={`phase ${p.state}`}>
              <div className="ph-row">
                <span className="ph-name">{p.phase}</span>
                <span className={`ph-state ${p.state}`}>{p.state.replace('_', ' ')}</span>
              </div>
              <div className="ph-output">{p.output}</div>
            </li>
          ))}
        </ol>
      ) : (
        <GraphView graph={graph} />
      )}
    </aside>
  )
}
