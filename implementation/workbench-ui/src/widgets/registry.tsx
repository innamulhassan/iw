// The widget registry — maps an event `kind` → a renderer component. The chat renders each event
// (in seq order) via WidgetView. A new widget type = add one renderer here; no core change. Trusted
// built-ins render directly; untrusted/agent HTML renders in a sandboxed iframe (the safety seam).
import type { FC } from 'react'
import type { ChatEvent, GraphSlice } from '../model'
import { GraphView } from '../components/GraphView'

type WidgetProps = { event: ChatEvent }

function TextWidget({ event }: WidgetProps) {
  return (
    <div className={`msg ${event.kind}`}>
      <span className="who">{event.actor ?? 'agent'}</span>
      <span className="body">{event.text}</span>
    </div>
  )
}

function ToolCallWidget({ event }: WidgetProps) {
  const p = event.payload ?? {}
  return (
    <div className="widget tool-call" data-testid="widget-tool_call">
      <div className="w-head">🔧 {p.capability ?? event.text}</div>
      {p.result && <div className="w-body">{p.result}</div>}
      {p.evidence && <a className="w-evidence" href={p.evidence}>{p.evidence}</a>}
    </div>
  )
}

function TableWidget({ event }: WidgetProps) {
  const p = event.payload ?? {}
  const cols: string[] = p.columns ?? []
  const rows: string[][] = p.rows ?? []
  return (
    <div className="widget table-w" data-testid="widget-table">
      {p.title && <div className="w-head">{p.title}</div>}
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

function ImageWidget({ event }: WidgetProps) {
  const p = event.payload ?? {}
  return (
    <div className="widget image-w" data-testid="widget-image">
      {event.text && <div className="w-head">{event.text}</div>}
      <img src={p.src} alt={p.alt ?? event.text ?? 'image'} />
    </div>
  )
}

function GraphWidget({ event }: WidgetProps) {
  const slice = event.payload as GraphSlice | undefined
  if (!slice?.nodes) return <div className="widget" data-testid="widget-graph">graph</div>
  return <div className="widget graph-w" data-testid="widget-graph"><GraphView graph={slice} /></div>
}

function HtmlWidget({ event }: WidgetProps) {
  // untrusted/agent-generated HTML → sandboxed iframe (no scripts, no same-origin). The safety seam.
  const html: string = event.payload?.html ?? ''
  return (
    <div className="widget html-w" data-testid="widget-html">
      {event.text && <div className="w-head">{event.text}</div>}
      <iframe title={`html-widget-${event.seq}`} sandbox="" srcDoc={html} className="w-iframe" />
    </div>
  )
}

function GateWidget({ event }: WidgetProps) {
  // a gate that arrives as a chat event (read-only summary; the live approval is the pendingGate card)
  const a = event.payload ?? {}
  return (
    <div className="widget gate-w" data-testid="widget-gate">
      <div className="w-head">⚠ {a.technique ?? 'action'} on {a.target}</div>
      {a.expected_effect && <div className="w-body">{a.expected_effect}</div>}
      {a.rollback && <div className="w-evidence">rollback: {a.rollback}</div>}
    </div>
  )
}

const WIDGETS: Record<string, FC<WidgetProps>> = {
  msg: TextWidget,
  agent: TextWidget,
  suggestion: TextWidget,
  decision: TextWidget,
  tool_call: ToolCallWidget,
  table: TableWidget,
  image: ImageWidget,
  graph: GraphWidget,
  html: HtmlWidget,
  gate: GateWidget,
}

export const WIDGET_KINDS = Object.keys(WIDGETS)

export function WidgetView({ event }: WidgetProps) {
  const Renderer = WIDGETS[event.kind] ?? TextWidget
  return <Renderer event={event} />
}
