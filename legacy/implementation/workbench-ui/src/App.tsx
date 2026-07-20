import { useCallback, useEffect, useMemo, useState } from 'react'
import { HttpApiClient } from './api/http'
import { Workbench } from './components/Workbench'

// Single-use, single-user demo. The operator REGISTERS browser capabilities — UI-only tools (e.g.
// ServiceNow, Google) given by {name, url, description, intent}. Each opens a real browser tab to log
// into; the agent then reads those live pages as the engine intents they back. The agent runs through
// the LangGraph engine and pauses at the write-gate (hosted here) for approval.
const API = 'http://127.0.0.1:8088'   // single-use demo backend (engine.api.serve)
const ACTOR = 'operator'

// the ASSESS-phase read intents a browser capability can back (from the playbook's phase.needs)
const INTENTS: [string, string][] = [
  ['incident-source', 'Incident source (the ticket)'],
  ['similar-incidents', 'Similar incidents'],
  ['change-history', 'Change history'],
  ['topology', 'Topology / CMDB'],
  ['telemetry', 'Telemetry'],
]

type Status = 'running' | 'waiting_approval' | 'done' | 'denied'

interface Cap {
  key: string; name: string; url: string; description: string; intents: string[]
  effect: string; ready: boolean; opened: boolean; reads: number; last_excerpt: string; wall: boolean
}

export default function App() {
  const [sid, setSid] = useState<string | null>(null)
  const [status, setStatus] = useState<Status>('running')
  const [gateId, setGateId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [caps, setCaps] = useState<Cap[]>([])
  const [browserMode, setBrowserMode] = useState<string | null>(null)
  // register form
  const [fName, setFName] = useState('')
  const [fUrl, setFUrl] = useState('')
  const [fDesc, setFDesc] = useState('')
  const [fIntent, setFIntent] = useState(INTENTS[0][0])

  const post = (path: string, body?: unknown) =>
    fetch(`${API}${path}`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${path} → ${r.status}: ${(await r.text()).slice(0, 160)}`)
      return r.json()
    })

  const refreshCaps = useCallback(async () => {
    try {
      const r = await fetch(`${API}/capabilities`)
      if (!r.ok) return
      const d = await r.json()
      setCaps(d.capabilities ?? [])
      setBrowserMode(d.browser_mode ?? null)
    } catch { /* backend may be starting */ }
  }, [])

  // poll capability status (reads counter ticks up live during a run)
  useEffect(() => {
    refreshCaps()
    const t = setInterval(refreshCaps, 2000)
    return () => clearInterval(t)
  }, [refreshCaps])

  const registerDemo = useCallback(async () => {
    setBusy(true); setErr(null)
    try { await post('/capabilities/demo'); await refreshCaps() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }, [refreshCaps])

  const registerCap = useCallback(async () => {
    if (!fName.trim() || !fUrl.trim()) return
    setBusy(true); setErr(null)
    try {
      await post('/capabilities', { name: fName.trim(), url: fUrl.trim(), description: fDesc.trim(), intents: [fIntent] })
      setFName(''); setFUrl(''); setFDesc('')
      await refreshCaps()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }, [fName, fUrl, fDesc, fIntent, refreshCaps])

  const setReady = useCallback(async (key: string, ready: boolean) => {
    try { await post(`/capabilities/${key}/ready`, { ready }); await refreshCaps() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
  }, [refreshCaps])

  const removeCap = useCallback(async (key: string) => {
    try { await fetch(`${API}/capabilities/${key}`, { method: 'DELETE' }); await refreshCaps() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
  }, [refreshCaps])

  const start = useCallback(async () => {
    setBusy(true); setErr(null)
    try {
      const runId = `INC-${Math.floor(1000 + Math.random() * 9000)}`
      const s = await post('/sessions', { domain: 'app-incident', id: runId, kind: 'incident', actor: ACTOR })
      const id = s.session_id as string
      const adv = await post(`/sessions/${id}/advance`)
      setSid(id); setStatus(adv.status); setGateId(adv.next?.[0] ?? null)
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }, [])

  const decide = useCallback(async (decision: 'approve' | 'deny') => {
    if (!sid || !gateId) return
    setBusy(true)
    try {
      const r = await post(`/sessions/${sid}/gate`, { actor: ACTOR, gate_id: gateId, decision })
      setStatus(r.status); setGateId(r.next?.[0] ?? null)
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }, [sid, gateId])

  const client = useMemo(() => (sid ? new HttpApiClient(API, sid, ACTOR) : null), [sid])
  const notReady = caps.filter((c) => !c.ready).length

  // ── run view ─────────────────────────────────────────────────────────
  if (client) {
    return (
      <div>
        {caps.length > 0 && (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
            padding: '7px 16px', background: '#f4f7fb', borderBottom: '1px solid #e4e9f0',
            fontFamily: 'system-ui', fontSize: 12.5 }}>
            <span style={{ fontWeight: 800, color: '#5a6678' }}>LIVE TOOLS</span>
            {caps.map((c) => (
              <span key={c.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                background: '#fff', border: '1px solid #e4e9f0', borderRadius: 20, padding: '3px 10px' }}>
                <b>{c.name}</b>
                <span style={{ color: '#94a0b0' }}>→ {c.intents.join(', ')}</span>
                <span style={{ color: c.reads > 0 ? '#1f8a4c' : '#94a0b0', fontWeight: 700 }}>
                  read {c.reads}×</span>
                {c.wall && <span title="bot/login wall seen" style={{ color: '#c2780c' }}>⚠</span>}
              </span>
            ))}
          </div>
        )}
        {status === 'waiting_approval' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '10px 16px',
            background: '#fff7ed', borderBottom: '2px solid #c2780c', fontFamily: 'system-ui' }}>
            <span style={{ fontWeight: 800, color: '#9a4a12' }}>⛔ Write-gate</span>
            <span style={{ color: '#5a6678', fontSize: 14 }}>
              The agent proposes a remediation write. Nothing changes until you approve.</span>
            <button onClick={() => decide('approve')} disabled={busy}
              style={{ marginLeft: 'auto', background: '#1f8a4c', color: '#fff', border: 0,
                borderRadius: 8, padding: '7px 16px', fontWeight: 700, cursor: 'pointer' }}>
              {busy ? 'Applying…' : 'Approve & apply'}</button>
            <button onClick={() => decide('deny')} disabled={busy}
              style={{ background: '#fff', color: '#cf3636', border: '1px solid #f0c2bd',
                borderRadius: 8, padding: '7px 14px', fontWeight: 700, cursor: 'pointer' }}>Deny</button>
          </div>
        )}
        <Workbench client={client} />
      </div>
    )
  }

  // ── start screen ─────────────────────────────────────────────────────
  const field = { width: '100%', border: '1px solid #e4e9f0', borderRadius: 8, padding: '8px 10px',
    fontSize: 13.5, boxSizing: 'border-box' as const }
  const card = { border: '1px solid #e4e9f0', borderRadius: 12, padding: 16, background: '#fff', marginTop: 14 }

  return (
    <div style={{ maxWidth: 720, margin: '7vh auto', padding: '0 24px', fontFamily: 'system-ui' }}>
      <h1 style={{ fontSize: 26, marginBottom: 6 }}>🔭 The Investigation Workbench</h1>
      <p style={{ color: '#5a6678', marginTop: 0 }}>
        Single-use demo — the agent (xAI Grok) investigates live through the LangGraph engine. Register
        the UI-only tools you use in a browser; the agent drives them as governed capabilities, builds
        the incident graph, ranks the cause, and pauses at the write-gate for your approval.
      </p>

      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 800, color: '#94a0b0', textTransform: 'uppercase', letterSpacing: '.05em' }}>
            Browser capabilities</div>
          <button onClick={registerDemo} disabled={busy}
            style={{ marginLeft: 'auto', background: '#eef3fb', color: '#2557a7', border: '1px solid #cfe0f5',
              borderRadius: 7, padding: '5px 11px', fontWeight: 700, fontSize: 12.5, cursor: 'pointer' }}>
            ✨ Load Google demo</button>
        </div>
        <p style={{ color: '#5a6678', fontSize: 13, margin: '6px 0 12px' }}>
          Each capability opens its own browser tab. <b>Log in there</b>, then mark it <b>Ready</b> — the
          agent waits for that before reading the live page. Not ready → it uses built-in demo data.
          {browserMode && <span style={{ color: '#94a0b0' }}> · browser: {browserMode}</span>}
        </p>

        {/* register form */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1.6fr', gap: 8 }}>
          <input value={fName} onChange={(e) => setFName(e.target.value)} placeholder="Name (e.g. ServiceNow)" style={field} />
          <input value={fUrl} onChange={(e) => setFUrl(e.target.value)} placeholder="https://your-instance.service-now.com/…" style={field} />
          <input value={fDesc} onChange={(e) => setFDesc(e.target.value)} placeholder="What it's for (optional)" style={field} />
          <div style={{ display: 'flex', gap: 8 }}>
            <select value={fIntent} onChange={(e) => setFIntent(e.target.value)} style={{ ...field, flex: 1 }}>
              {INTENTS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
            <button onClick={registerCap} disabled={busy || !fName.trim() || !fUrl.trim()}
              style={{ background: '#2557a7', color: '#fff', border: 0, borderRadius: 8, padding: '0 16px',
                fontWeight: 700, fontSize: 13.5, cursor: fName.trim() && fUrl.trim() ? 'pointer' : 'default',
                whiteSpace: 'nowrap' }}>Register &amp; open</button>
          </div>
        </div>

        {/* registered list */}
        {caps.length > 0 && (
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {caps.map((c) => (
              <div key={c.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px',
                border: '1px solid #e9eef5', borderRadius: 9, background: '#fbfcfe' }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>
                    {c.name}
                    <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 700, color: '#2557a7',
                      background: '#eef3fb', borderRadius: 5, padding: '1px 7px' }}>{c.intents.join(' · ')}</span>
                    {c.wall && <span style={{ marginLeft: 6, fontSize: 11.5, color: '#c2780c' }}>⚠ wall — solve in tab</span>}
                  </div>
                  <div style={{ color: '#94a0b0', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.url}{c.reads > 0 && <span style={{ color: '#1f8a4c' }}> · read {c.reads}×</span>}
                  </div>
                </div>
                <button onClick={() => setReady(c.key, !c.ready)}
                  style={{ background: c.ready ? '#e8f6ee' : '#fff', color: c.ready ? '#1f8a4c' : '#5a6678',
                    border: `1px solid ${c.ready ? '#bfe6cd' : '#e4e9f0'}`, borderRadius: 7, padding: '6px 11px',
                    fontWeight: 700, fontSize: 12.5, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                  {c.ready ? '✓ Ready' : "I've logged in"}</button>
                <button onClick={() => removeCap(c.key)} title="remove"
                  style={{ background: '#fff', color: '#cf3636', border: '1px solid #f0c2bd', borderRadius: 7,
                    padding: '6px 9px', fontWeight: 700, fontSize: 12.5, cursor: 'pointer' }}>✕</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700 }}>payments-api latency</div>
        <div style={{ color: '#5a6678', fontSize: 14, margin: '4px 0 12px' }}>
          payments-api p99 latency 4.2s (was 260ms) on /charge — investigate root cause &amp; remediate.
        </div>
        {notReady > 0 && (
          <div style={{ color: '#9a4a12', fontSize: 12.5, marginBottom: 10 }}>
            {notReady} capabilit{notReady === 1 ? 'y is' : 'ies are'} not Ready — the agent will wait briefly,
            then use demo data for {notReady === 1 ? 'it' : 'them'}.
          </div>
        )}
        <button onClick={start} disabled={busy}
          style={{ background: '#2557a7', color: '#fff', border: 0, borderRadius: 9, padding: '10px 18px',
            fontWeight: 700, fontSize: 14, cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.7 : 1 }}>
          {busy ? 'Agent is investigating…' : 'Start investigation'}</button>
      </div>
      {err && <p style={{ color: '#cf3636', fontSize: 13, marginTop: 12 }}>{err}</p>}
    </div>
  )
}
