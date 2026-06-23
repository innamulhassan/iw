// The focused investigation graph (design B9.3 / F1). Conveys hundreds of nodes: the cause path +
// impacted neighbours in full, a minimap for density, and a collapsed count for the healthy rest.
import type { GraphSlice } from '../model'

const COLOR: Record<string, string> = {
  cause: '#C0392B',
  suspect: '#E67E22',
  impacted: '#2E86C1',
  healthy: '#7F8C8D',
}

const short = (id: string) => id.split(':')[1] ?? id

export function GraphView({ graph }: { graph: GraphSlice }) {
  const path = graph.causePath
  const W = 580
  const H = 300
  const n = path.length
  if (n === 0) return <div className="graphview" data-testid="graph-view">No cause path yet.</div>
  const x = (i: number) => 44 + i * ((W - 200) / Math.max(1, n - 1))
  const y = 96
  const byId = new Map(graph.nodes.map((g) => [g.id, g]))
  const impacted = graph.nodes.filter((g) => g.state === 'impacted' && !path.includes(g.id))

  return (
    <div className="graphview" data-testid="graph-view">
      <div className="graph-head">
        <span className="g-total">{graph.total} nodes in scope</span>
        <span className="g-impacted">{graph.impacted} impacted</span>
        <span className="g-collapsed">+{graph.collapsed.count} healthy · collapsed</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="investigation graph — cause path">
        {path.slice(0, -1).map((id, i) => (
          <line key={`e-${id}`} x1={x(i)} y1={y} x2={x(i + 1)} y2={y} className="g-edge" />
        ))}
        {path.map((id, i) => {
          const st = byId.get(id)?.state ?? 'healthy'
          return (
            <g key={id}>
              <circle cx={x(i)} cy={y} r={17} fill={COLOR[st]} />
              <text x={x(i)} y={y + 36} textAnchor="middle" className="g-label">{short(id)}</text>
            </g>
          )
        })}
        {impacted.map((g, i) => (
          <g key={g.id}>
            <line x1={x(2)} y1={y} x2={x(2) + (i - 1) * 78} y2={y + 78} className="g-edge faint" />
            <circle cx={x(2) + (i - 1) * 78} cy={y + 78} r={11} fill={COLOR.impacted} opacity={0.85} />
            <text x={x(2) + (i - 1) * 78} y={y + 100} textAnchor="middle" className="g-label small">{short(g.id)}</text>
          </g>
        ))}
        <g transform={`translate(${W - 132}, 14)`} data-testid="minimap">
          {/* density driven by the SLICE (not a hardcoded fixture), bounded so any incident fits */}
          <rect width={120} height={74} className="mm-bg" rx={4} />
          {Array.from({ length: Math.min(graph.collapsed.count, 27 * 12) }).map((_, i) => (
            <rect key={i} x={6 + (i % 27) * 4.1} y={8 + Math.floor(i / 27) * 5.4} width={2.8} height={3.6} className="mm-dot" />
          ))}
          {Array.from({ length: Math.min(graph.impacted, 27) }).map((_, i) => (
            <rect key={`hot-${i}`} x={6 + i * 4.1} y={8 + 4 * 5.4} width={2.8} height={3.6} className="mm-hot" />
          ))}
          <text x={60} y={70} textAnchor="middle" className="mm-label">{graph.total} nodes</text>
        </g>
      </svg>
      <div className="graph-legend">
        <span><i className="dot cause" />cause</span>
        <span><i className="dot suspect" />suspect</span>
        <span><i className="dot impacted" />impacted</span>
        <span className="collapsed-chip">+{graph.collapsed.count} collapsed</span>
      </div>
    </div>
  )
}
