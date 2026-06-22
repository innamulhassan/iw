// Center pane — the shared chat. Each event renders via the widget registry (text · tool-call ·
// table · image · graph · html). The composer + the gate are gated to the pen-holder (writer);
// viewers see them disabled.
import { useState } from 'react'
import type { ChatEvent, PendingGate, Role } from '../model'
import { WidgetView } from '../widgets/registry'

type Decision = 'approve' | 'refine' | 'deny'

export function TriagePane({ chat, gate, role, onSend, onGate }: {
  chat: ChatEvent[]
  gate: PendingGate | null
  role: Role
  onSend: (text: string) => void
  onGate: (decision: Decision) => void
}) {
  const [text, setText] = useState('')
  const isWriter = role === 'writer'

  return (
    <section className="pane triage" data-testid="triage-pane">
      <h2>Triage</h2>
      <div className="chat">
        {chat.map((e) => (
          <div key={e.seq} className="chat-item">
            <WidgetView event={e} />
          </div>
        ))}
        {gate && (
          <div className="gate-card" data-testid="gate-card">
            <div className="gate-head">⚠ Approval required — this is a write</div>
            <div className="gate-body">
              <p><b>{gate.action.technique}</b> on <code>{gate.action.target}</code></p>
              <ul>
                <li><b>Effect:</b> {gate.action.expected_effect}</li>
                <li><b>Blast radius:</b> {gate.action.blast_radius}</li>
                <li><b>Rollback:</b> {gate.action.rollback}</li>
                <li>{gate.action.temporary ? 'Temporary — must be reverted before close' : 'Permanent'}</li>
              </ul>
            </div>
            <div className="gate-actions">
              <button className="approve" disabled={!isWriter} onClick={() => onGate('approve')}>Approve</button>
              <button className="refine" disabled={!isWriter} onClick={() => onGate('refine')}>Refine</button>
              <button className="deny" disabled={!isWriter} onClick={() => onGate('deny')}>Deny</button>
            </div>
            {!isWriter && <div className="gate-note">Only the pen-holder can approve.</div>}
          </div>
        )}
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          const t = text.trim()
          if (t && isWriter) {
            onSend(t)
            setText('')
          }
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={isWriter ? 'Message the investigation…' : 'Take the pen to write…'}
          aria-label="message"
          disabled={!isWriter}
        />
        <button type="submit" disabled={!isWriter}>Send</button>
      </form>
    </section>
  )
}
