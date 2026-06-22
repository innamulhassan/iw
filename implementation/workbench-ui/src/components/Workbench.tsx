// The workbench shell — one app, three panes. Live updates: if a `streamUrl` is configured (real
// backend) the chat is driven by SSE (api/stream.ts, auto-reconnect + Last-Event-ID resume);
// polling runs as the snapshot/fallback. The pen badge shows writer vs viewer; viewers can take the
// pen, the writer can release it.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient, ChatEvent, IncidentView } from '../model'
import { connectStream } from '../api/stream'
import { IncidentsPane } from './IncidentsPane'
import { RightPane } from './RightPane'
import { TriagePane } from './TriagePane'

function mergeBySeq(existing: ChatEvent[], incoming: ChatEvent[]): ChatEvent[] {
  const seen = new Set(existing.map((e) => e.seq))
  const fresh = incoming.filter((e) => !seen.has(e.seq))
  return fresh.length ? [...existing, ...fresh] : existing
}

export function Workbench({ client, pollMs = 4000, streamUrl }: {
  client: ApiClient
  pollMs?: number
  streamUrl?: string
}) {
  const [view, setView] = useState<IncidentView | null>(null)
  const seqRef = useRef(0)
  const mounted = useRef(true)
  const sid = view ? `${view.subject.domain}:${view.subject.id}` : null

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(async () => {
    const v = await client.getIncident()
    if (!mounted.current) return
    seqRef.current = v.chat.reduce((m, e) => Math.max(m, e.seq), 0)
    setView(v)
  }, [client])

  const poll = useCallback(async () => {
    const r = await client.poll(seqRef.current)
    if (!mounted.current) return
    seqRef.current = r.seq
    setView((prev) =>
      prev
        ? {
            ...prev,
            chat: mergeBySeq(prev.chat, r.events),
            phases: r.phases,
            graph: r.graph,
            pendingGate: r.pendingGate,
            role: r.role,
            penHolder: r.penHolder,
          }
        : prev,
    )
  }, [client])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const id = setInterval(() => void poll(), pollMs)
    return () => clearInterval(id)
  }, [poll, pollMs])

  // live chat over SSE when a stream URL is configured (real backend); merged by seq alongside poll
  useEffect(() => {
    if (!streamUrl || !sid) return
    return connectStream(streamUrl, sid, (ev) => {
      if (!mounted.current) return
      seqRef.current = Math.max(seqRef.current, ev.seq)
      setView((prev) => (prev ? { ...prev, chat: mergeBySeq(prev.chat, [ev]) } : prev))
    })
  }, [streamUrl, sid])

  if (!view) return <div className="loading">Loading…</div>

  const isWriter = view.role === 'writer'

  return (
    <div className="workbench">
      <header className="topbar">
        <span className="brand">Incident Triage — Investigation Engine</span>
        <span className="subject">
          {view.subject.id} · <span className={`sev sev-${view.severity}`}>{view.severity}</span> · {view.symptom}
        </span>
        <span className="pen" data-testid="pen-badge">
          {isWriter ? (
            <>
              <b>✎ You have the pen</b>
              <button className="take-pen" onClick={() => client.releasePen().then(() => poll())}>Release</button>
            </>
          ) : (
            <>
              👁 Viewing — <b>{view.penHolder}</b> has the pen
              <button className="take-pen" onClick={() => client.takePen().then(() => poll())}>Take the pen</button>
            </>
          )}
        </span>
      </header>
      <div className="panes">
        <IncidentsPane incidents={view.incidents} />
        <TriagePane
          chat={view.chat}
          gate={view.pendingGate}
          role={view.role}
          onSend={(t) => client.postMessage(t).then(() => poll())}
          onGate={(d) => view.pendingGate && client.answerGate(view.pendingGate.gate_id, d).then(() => poll())}
        />
        <RightPane phases={view.phases} graph={view.graph} />
      </div>
    </div>
  )
}
