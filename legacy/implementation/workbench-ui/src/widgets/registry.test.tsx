// Widget registry — each event kind renders via its component; unknown kinds fall back to text;
// untrusted HTML renders in a sandboxed iframe.
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WidgetView } from './registry'

describe('widget registry', () => {
  it('renders a text message', () => {
    render(<WidgetView event={{ seq: 1, kind: 'msg', actor: 'j.rivera', text: 'hello' }} />)
    expect(screen.getByText('hello')).toBeInTheDocument()
  })

  it('renders a tool-call widget', () => {
    render(<WidgetView event={{ seq: 2, kind: 'tool_call', text: 'traces',
      payload: { capability: 'otel__traces', result: 'DB span 75%', evidence: 'otel://9af3' } }} />)
    const w = screen.getByTestId('widget-tool_call')
    expect(w).toHaveTextContent('otel__traces')
    expect(w).toHaveTextContent('DB span 75%')
  })

  it('renders a table widget', () => {
    render(<WidgetView event={{ seq: 3, kind: 'table',
      payload: { title: 'Candidates', columns: ['cause', 'node'], rows: [['disk failure', 'stor:pay-vol']] } }} />)
    const w = screen.getByTestId('widget-table')
    expect(w).toHaveTextContent('cause')
    expect(w).toHaveTextContent('stor:pay-vol')
  })

  it('renders an image widget', () => {
    render(<WidgetView event={{ seq: 4, kind: 'image', text: 'p99', payload: { src: 'data:,', alt: 'p99 chart' } }} />)
    expect(screen.getByAltText('p99 chart')).toBeInTheDocument()
  })

  it('renders untrusted HTML inside a sandboxed iframe', () => {
    render(<WidgetView event={{ seq: 5, kind: 'html', payload: { html: '<b>hi</b>' } }} />)
    const iframe = screen.getByTitle('html-widget-5')
    expect(iframe).toHaveAttribute('sandbox', '')          // no scripts, no same-origin
    expect(iframe).toHaveAttribute('srcdoc', '<b>hi</b>')
  })

  it('renders a gate widget (no fallback to text)', () => {
    render(<WidgetView event={{ seq: 6, kind: 'gate',
      payload: { technique: 'failover', target: 'db:payments-ora', expected_effect: 'served by standby' } }} />)
    const w = screen.getByTestId('widget-gate')
    expect(w).toHaveTextContent('failover')
    expect(w).toHaveTextContent('db:payments-ora')
  })
})
