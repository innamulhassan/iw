// The data layer — TS shapes (mirroring the engine's read-model) + INC-4821 fixtures + a mock API
// client. The UI talks to ApiClient; MockApiClient serves fixtures so the console runs with no
// backend. The real client hits the FastAPI surface (SSE /stream + POST) behind the same interface.

export interface SubjectRef { domain: string; id: string; kind: string }

export interface IncidentListItem {
  id: string
  title: string
  severity: string
  state: string
  relation?: 'subject' | 'related' | 'similar'
}

export interface PhaseSummary {
  id: string
  phase: string
  state: 'active' | 'waiting_approval' | 'waiting_input' | 'blocked' | 'done' | 'failed'
  output?: string
}

// Every chat item is a typed event ordered by seq. `kind` selects the widget renderer; `payload`
// carries the rich-widget data. A new widget type = add a renderer to the registry — no core change.
export type WidgetKind =
  | 'msg' | 'agent' | 'suggestion' | 'decision'                  // text-ish
  | 'tool_call' | 'gate' | 'image' | 'table' | 'graph' | 'html'  // rich widgets

export interface ChatEvent {
  seq: number
  kind: WidgetKind
  actor?: string
  text?: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload?: any
}

export interface GraphNode {
  id: string
  kind: string
  type: string
  layer?: string
  labels?: string[]
  state?: 'cause' | 'suspect' | 'impacted' | 'healthy'
}

export interface GraphSlice {
  total: number
  impacted: number
  causePath: string[]
  nodes: GraphNode[]
  collapsed: { count: number; summary: string }
}

export interface GateAction {
  technique: string
  target: string
  expected_effect: string
  blast_radius: string
  rollback: string
  temporary: boolean
}

export interface PendingGate { gate_id: string; action: GateAction }

export type Role = 'writer' | 'viewer'

export interface IncidentView {
  subject: SubjectRef
  symptom: string
  severity: string
  role: Role
  penHolder: string
  incidents: IncidentListItem[]
  phases: PhaseSummary[]
  chat: ChatEvent[]
  graph: GraphSlice
  pendingGate: PendingGate | null
}

// ── INC-4821 fixtures ───────────────────────────────────────────────────
const CAUSE_PATH = ['biz:checkout-journey', 'svc:checkout', 'app:payments-api', 'db:payments-ora', 'stor:pay-vol']

function sliceNodes(): GraphNode[] {
  return [
    { id: 'biz:checkout-journey', kind: 'system', type: 'journey', layer: 'business', state: 'impacted' },
    { id: 'svc:checkout', kind: 'system', type: 'app', layer: 'app', state: 'impacted' },
    { id: 'app:payments-api', kind: 'system', type: 'app', layer: 'app', labels: ['pci', 'tier-0'], state: 'suspect' },
    { id: 'db:payments-ora', kind: 'system', type: 'database', layer: 'database', state: 'impacted' },
    { id: 'stor:pay-vol', kind: 'system', type: 'storage', layer: 'storage', labels: ['suspect'], state: 'cause' },
    { id: 'app:cart-api', kind: 'system', type: 'app', layer: 'app', state: 'impacted' },
    { id: 'chg:deploy-rev47', kind: 'change', type: 'change', state: 'healthy' },
  ]
}

const SPARK = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="56"><polyline points="0,46 120,44 132,8 220,6" ' +
  'fill="none" stroke="#C0392B" stroke-width="2"/></svg>',
)

export const INC4821: IncidentView = {
  subject: { domain: 'app-incident', id: 'INC-4821', kind: 'incident' },
  symptom: 'checkout p99 0.4s → 4.2s, errors 18%',
  severity: 'P1',
  role: 'writer',
  penHolder: 'you',
  incidents: [
    { id: 'INC-4821', title: 'Checkout latency + errors', severity: 'P1', state: 'triage', relation: 'subject' },
    { id: 'INC-4820', title: 'Payments DB I/O spike (yesterday)', severity: 'P2', state: 'closed', relation: 'similar' },
    { id: 'INC-4822', title: 'Cart timeouts', severity: 'P3', state: 'triage', relation: 'related' },
  ],
  phases: [
    { id: 'INC-4821:assess:1', phase: 'assess', state: 'done', output: '147 nodes in scope · 12 impacted · suggestion from INC-4820' },
    { id: 'INC-4821:root-cause:1', phase: 'root-cause', state: 'done', output: 'stor:pay-vol disk failure (0.9) · rev47/gemini/network ruled out' },
    { id: 'INC-4821:remediation:1', phase: 'remediation', state: 'waiting_approval', output: 'failover db → standby (temporary) — awaiting approval' },
    { id: 'INC-4821:verify-close:1', phase: 'verify-close', state: 'active', output: '—' },
  ],
  chat: [
    { seq: 1, kind: 'msg', actor: 'j.rivera', text: 'Checkout is throwing errors and slow.' },
    { seq: 2, kind: 'agent', text: 'Assessed: 147 nodes in scope, 12 impacted. Symptom: checkout p99 0.4s→4.2s, errors 18%. Similar prior INC-4820 suggests a DB-path cause.' },
    { seq: 3, kind: 'tool_call', actor: 'agent', text: 'traces(checkout)',
      payload: { capability: 'otel__traces', result: 'DB span = 75% of latency (3140ms) — not app code', evidence: 'otel://trace/9af3' } },
    { seq: 4, kind: 'table', text: 'Root-cause candidates',
      payload: { title: 'Root-cause candidates', columns: ['cause', 'node', 'conf'],
        rows: [['pay-vol disk failure → RAID rebuild → DB I/O', 'stor:pay-vol', '0.90']] } },
    { seq: 5, kind: 'image', text: 'checkout p99 (last 30m)', payload: { alt: 'p99 0.4s→4.2s', src: SPARK } },
    { seq: 6, kind: 'suggestion', text: 'Recommend: failover DB to standby (temporary); durable fix = replace disk 1.4.7.' },
  ],
  graph: {
    total: 147,
    impacted: 12,
    causePath: CAUSE_PATH,
    nodes: sliceNodes(),
    collapsed: { count: 135, summary: '135 healthy / ruled-out · collapsed' },
  },
  pendingGate: {
    gate_id: 'INC-4821-a1',
    action: {
      technique: 'failover',
      target: 'db:payments-ora',
      expected_effect: 'DB served by standby; I/O normal',
      blast_radius: 'payments read path',
      rollback: 'fail back once disk replaced',
      temporary: true,
    },
  },
}

// One round-trip: new events since the client's seq + the read-model snapshot + role/pen. Works for
// polling; the SSE client (api/stream.ts) delivers the same events live over text/event-stream.
export interface PollResult {
  events: ChatEvent[]
  seq: number
  role: Role
  penHolder: string
  phases: PhaseSummary[]
  graph: GraphSlice
  pendingGate: PendingGate | null
}

export interface PenState { role: Role; penHolder: string }

export interface ApiClient {
  getIncident(): Promise<IncidentView>
  poll(afterSeq: number): Promise<PollResult>
  postMessage(text: string): Promise<ChatEvent>
  answerGate(gateId: string, decision: 'approve' | 'refine' | 'deny'): Promise<ChatEvent>
  takePen(): Promise<PenState>
  releasePen(): Promise<PenState>
}

export class MockApiClient implements ApiClient {
  private view: IncidentView
  private seq = 6

  constructor(role: Role = 'writer') {
    this.view = structuredClone(INC4821)
    this.view.role = role
    this.view.penHolder = role === 'writer' ? 'you' : 'j.rivera'
  }

  async getIncident(): Promise<IncidentView> {
    return structuredClone(this.view)
  }

  async poll(afterSeq: number): Promise<PollResult> {
    return {
      events: this.view.chat.filter((e) => e.seq > afterSeq).map((e) => structuredClone(e)),
      seq: this.seq,
      role: this.view.role,
      penHolder: this.view.penHolder,
      phases: structuredClone(this.view.phases),
      graph: structuredClone(this.view.graph),
      pendingGate: this.view.pendingGate ? structuredClone(this.view.pendingGate) : null,
    }
  }

  /** Test/demo helper — simulate the backend appending an event (picked up by the next poll/stream). */
  emit(text: string, kind: WidgetKind = 'agent', payload?: unknown): void {
    this.view.chat.push({ seq: ++this.seq, kind, text, payload })
  }

  async postMessage(text: string): Promise<ChatEvent> {
    const ev: ChatEvent = { seq: ++this.seq, kind: 'msg', actor: 'you', text }
    this.view.chat.push(ev)
    return ev
  }

  async answerGate(gateId: string, decision: 'approve' | 'refine' | 'deny'): Promise<ChatEvent> {
    this.view.pendingGate = null
    const rem = this.view.phases.find((p) => p.phase === 'remediation')
    if (rem) rem.state = decision === 'approve' ? 'done' : 'blocked'
    const ev: ChatEvent = {
      seq: ++this.seq,
      kind: 'decision',
      actor: 'you',
      text: decision === 'approve'
        ? `Approved gate ${gateId} — failover applied; I/O 28ms→4ms.`
        : `${decision} on gate ${gateId}.`,
    }
    this.view.chat.push(ev)
    return ev
  }

  async takePen(): Promise<PenState> {
    this.view.role = 'writer'
    this.view.penHolder = 'you'
    return { role: 'writer', penHolder: 'you' }
  }

  async releasePen(): Promise<PenState> {
    this.view.role = 'viewer'
    this.view.penHolder = 'j.rivera'
    return { role: 'viewer', penHolder: 'j.rivera' }
  }
}
