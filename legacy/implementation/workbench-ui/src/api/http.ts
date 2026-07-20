// The real HTTP client — hits the FastAPI surface behind the same ApiClient interface the UI uses
// (MockApiClient is the no-backend twin). It maps the wire shape (snake_case read-model + /poll) to
// the UI types, passes `?actor=` on every read (the server re-checks membership, AC9), and derives
// `role` from pen_holder when the wire omits it so the writer's composer is never wrongly disabled.
//
// NOTE: the read-model (D6) does not yet carry the rich GraphSlice / severity / open-gate action, so
// those are mapped best-effort (minimal slice, no pendingGate). Full read-model fidelity is a P9 item.
import type {
  ApiClient, ChatEvent, GraphSlice, IncidentView, PenState, PhaseSummary, PollResult, Role,
} from '../model'

interface WireIncident {
  _id: string
  domain: string
  subject: { domain: string; id: string; kind: string }
  state?: string
  symptom?: string
  phases?: { id: string; phase: string; state: string }[]
  graph?: { node_count: number; nodes: string[] }
}

interface WirePoll {
  events: ChatEvent[]
  seq: number
  status: string
  pen_holder: string | null
  role?: Role
  incident?: WireIncident | null
}

function mapGraph(doc?: WireIncident | null): GraphSlice {
  const g = doc?.graph
  return {
    total: g?.node_count ?? 0,
    impacted: 0,                                   // rich slice not in the read-model yet (D6 — P9)
    causePath: [],
    nodes: (g?.nodes ?? []).map((id) => ({ id, kind: '', type: '' })),
    collapsed: { count: 0, summary: '' },
  }
}

function mapPhases(doc?: WireIncident | null): PhaseSummary[] {
  return (doc?.phases ?? []).map((p) => ({ id: p.id, phase: p.phase, state: p.state as PhaseSummary['state'] }))
}

export class HttpApiClient implements ApiClient {
  constructor(private baseUrl: string, private sid: string, private actor: string) {}

  private base(path: string): string {
    return `${this.baseUrl}/sessions/${encodeURIComponent(this.sid)}/${path}`
  }

  private async getJson<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
    const q = new URLSearchParams({ actor: this.actor })
    for (const [k, v] of Object.entries(params)) q.set(k, String(v))
    const r = await fetch(`${this.base(path)}?${q}`)
    if (!r.ok) throw new Error(`${path} → ${r.status}`)
    return r.json() as Promise<T>
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const r = await fetch(this.base(path), {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error(`${path} → ${r.status}`)
    return r.json() as Promise<T>
  }

  private roleFrom(w: { role?: Role; pen_holder: string | null }): Role {
    return w.role ?? (w.pen_holder === this.actor ? 'writer' : 'viewer')
  }

  async poll(afterSeq: number): Promise<PollResult> {
    const w = await this.getJson<WirePoll>('poll', { after_seq: afterSeq })
    return {
      events: w.events ?? [],
      seq: w.seq,
      role: this.roleFrom(w),
      penHolder: w.pen_holder ?? '',
      phases: mapPhases(w.incident),
      graph: mapGraph(w.incident),
      pendingGate: null,                            // read-model carries no open-gate action yet (D6)
    }
  }

  async getIncident(): Promise<IncidentView> {
    const w = await this.getJson<WirePoll>('poll', { after_seq: 0 })
    const doc = w.incident ?? undefined
    return {
      subject: doc?.subject ?? { domain: '', id: this.sid, kind: 'incident' },
      symptom: doc?.symptom ?? '',
      severity: 'P?',                               // not in the read-model yet (D6 — P9)
      role: this.roleFrom(w),
      penHolder: w.pen_holder ?? '',
      incidents: doc
        ? [{ id: doc._id, title: doc.symptom ?? doc._id, severity: 'P?',
             state: doc.state ?? 'triage', relation: 'subject' }]
        : [],
      phases: mapPhases(doc),
      chat: w.events ?? [],
      graph: mapGraph(doc),
      pendingGate: null,
    }
  }

  async postMessage(text: string): Promise<ChatEvent> {
    const r = await this.post<{ seq: number }>('messages', { actor: this.actor, text })
    return { seq: r.seq, kind: 'msg', actor: this.actor, text }
  }

  async answerGate(gateId: string, decision: 'approve' | 'refine' | 'deny'): Promise<ChatEvent> {
    await this.post('gate', { actor: this.actor, gate_id: gateId, decision })
    return { seq: 0, kind: 'decision', actor: this.actor, text: `${decision} on gate ${gateId}` }
  }

  async takePen(): Promise<PenState> {
    const r = await this.post<{ pen_holder: string | null; role?: Role }>('take-pen', { actor: this.actor })
    return { role: this.roleFrom(r), penHolder: r.pen_holder ?? '' }
  }

  async releasePen(): Promise<PenState> {
    const r = await this.post<{ pen_holder: string | null }>('release-pen', { actor: this.actor })
    return { role: this.roleFrom(r), penHolder: r.pen_holder ?? '' }
  }
}
