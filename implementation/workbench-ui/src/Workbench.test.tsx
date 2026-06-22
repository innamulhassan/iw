// P7 · workbench component tests (vitest + React Testing Library).
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Workbench } from './components/Workbench'
import { MockApiClient } from './model'

const renderWorkbench = () => render(<Workbench client={new MockApiClient()} />)

describe('Workbench', () => {
  it('renders the three panes', async () => {
    renderWorkbench()
    expect(await screen.findByTestId('incidents-pane')).toBeInTheDocument()
    expect(screen.getByTestId('triage-pane')).toBeInTheDocument()
    expect(screen.getByTestId('right-pane')).toBeInTheDocument()
  })

  it('shows the INC-4821 subject + symptom and the related/similar incidents', async () => {
    renderWorkbench()
    expect(await screen.findByText(/checkout p99 0.4s → 4.2s/)).toBeInTheDocument()
    expect(screen.getByText('INC-4820')).toBeInTheDocument()      // surfaced prior
    expect(screen.getByText('similar')).toBeInTheDocument()
    expect(screen.getByText(/no auto-merge/)).toBeInTheDocument() // operator-controlled
  })

  it('lists the four phases with their state', async () => {
    renderWorkbench()
    const list = await screen.findByTestId('phases-list')
    expect(list).toHaveTextContent('assess')
    expect(list).toHaveTextContent('root-cause')
    expect(list).toHaveTextContent('remediation')
    expect(list).toHaveTextContent('verify-close')
  })

  it('switches to the graph view and conveys the 147-node scale', async () => {
    renderWorkbench()
    await screen.findByTestId('right-pane')
    fireEvent.click(screen.getByRole('tab', { name: 'Graph' }))
    expect(await screen.findByTestId('graph-view')).toBeInTheDocument()
    expect(screen.getByText('147 nodes in scope')).toBeInTheDocument()
    expect(screen.getByText('+135 healthy · collapsed')).toBeInTheDocument()
    expect(screen.getByText('pay-vol')).toBeInTheDocument()       // the cause node, in full
  })

  it('shows the inline gate and resolves it on approve', async () => {
    renderWorkbench()
    const gate = await screen.findByTestId('gate-card')
    expect(within(gate).getByText(/failover/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.queryByTestId('gate-card')).not.toBeInTheDocument())
    expect(await screen.findByText(/failover applied/)).toBeInTheDocument()
  })

  it('appends an operator message to the chat', async () => {
    renderWorkbench()
    const input = await screen.findByLabelText('message')
    fireEvent.change(input, { target: { value: 'check the cache too' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('check the cache too')).toBeInTheDocument()
  })

  it('picks up a new agent event by polling — no websocket/redis', async () => {
    const client = new MockApiClient()
    render(<Workbench client={client} pollMs={40} />)
    await screen.findByTestId('triage-pane')
    client.emit('Verified: recovery holds — p99 back to 260ms.')   // backend appends out-of-band
    expect(await screen.findByText(/Verified: recovery holds/)).toBeInTheDocument()
  })

  it('renders rich widgets in the chat (tool-call · table · image)', async () => {
    render(<Workbench client={new MockApiClient()} />)
    expect(await screen.findByTestId('widget-tool_call')).toHaveTextContent('otel__traces')
    expect(screen.getByTestId('widget-table')).toHaveTextContent('Root-cause candidates')
    expect(screen.getByTestId('widget-image')).toBeInTheDocument()
  })

  it('writer sees the pen and an enabled composer', async () => {
    render(<Workbench client={new MockApiClient('writer')} />)
    expect(await screen.findByTestId('pen-badge')).toHaveTextContent('You have the pen')
    expect(screen.getByLabelText('message')).not.toBeDisabled()
  })

  it('viewer is read-only and can take the pen', async () => {
    render(<Workbench client={new MockApiClient('viewer')} pollMs={40} />)
    const badge = await screen.findByTestId('pen-badge')
    expect(badge).toHaveTextContent('Viewing')
    expect(screen.getByLabelText('message')).toBeDisabled()              // composer disabled
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()  // gate disabled
    fireEvent.click(screen.getByRole('button', { name: 'Take the pen' }))
    await waitFor(() => expect(screen.getByTestId('pen-badge')).toHaveTextContent('You have the pen'))
    expect(screen.getByLabelText('message')).not.toBeDisabled()
  })
})
