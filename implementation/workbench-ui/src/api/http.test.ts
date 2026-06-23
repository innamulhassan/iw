// HttpApiClient — the real client maps the wire shape (snake_case + /poll) to the UI types, derives
// role from pen_holder when omitted, and passes ?actor= on reads.
import { describe, expect, it, vi } from 'vitest'
import { HttpApiClient } from './http'

function stubFetch(json: unknown) {
  return vi.fn((..._args: unknown[]) => Promise.resolve({ ok: true, json: async () => json } as Response))
}

const WIRE = {
  events: [{ seq: 5, kind: 'agent', text: 'hi' }],
  seq: 5,
  status: 'waiting_approval',
  pen_holder: 'alice',
  role: 'writer',
  incident: {
    _id: 'INC-1', domain: 'd', subject: { domain: 'd', id: 'INC-1', kind: 'incident' },
    state: 'waiting_approval', symptom: 'slow',
    phases: [{ id: 'a:1', phase: 'assess', state: 'done' }],
    graph: { node_count: 9, nodes: ['a', 'b'] },
  },
}

describe('HttpApiClient', () => {
  it('maps the /poll wire shape to PollResult', async () => {
    globalThis.fetch = stubFetch(WIRE) as unknown as typeof fetch
    const r = await new HttpApiClient('http://x', 'INC-1', 'alice').poll(0)
    expect(r.role).toBe('writer')
    expect(r.penHolder).toBe('alice')
    expect(r.seq).toBe(5)
    expect(r.graph.total).toBe(9)
    expect(r.phases[0].phase).toBe('assess')
    expect(r.events[0].text).toBe('hi')
  })

  it('derives role from pen_holder when the wire omits it', async () => {
    globalThis.fetch = stubFetch({ events: [], seq: 0, status: 'new', pen_holder: 'bob', incident: null }) as unknown as typeof fetch
    // actor (alice) != pen_holder (bob) → viewer
    expect((await new HttpApiClient('http://x', 'INC-1', 'alice').poll(0)).role).toBe('viewer')
  })

  it('passes ?actor= and ?after_seq= on reads', async () => {
    const f = stubFetch({ events: [], seq: 0, status: 'new', pen_holder: null, incident: null })
    globalThis.fetch = f as unknown as typeof fetch
    await new HttpApiClient('http://x', 'INC-1', 'alice').poll(3)
    const url = String(f.mock.calls[0][0])
    expect(url).toContain('actor=alice')
    expect(url).toContain('after_seq=3')
  })
})
