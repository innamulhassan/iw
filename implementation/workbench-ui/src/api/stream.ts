// SSE client — the production transport. The browser's EventSource auto-reconnects and resumes via
// Last-Event-ID (the event `seq`), so the live stream is gap-free over the durable event log. Each
// SSE message is a ChatEvent; the caller merges it by seq and re-renders. This is a drop-in swap for
// the polling loop in Workbench — same events, same widget registry, just live.
import type { ChatEvent } from '../model'

export function connectStream(
  baseUrl: string,
  sessionId: string,
  onEvent: (event: ChatEvent) => void,
): () => void {
  const url = `${baseUrl}/sessions/${encodeURIComponent(sessionId)}/stream`
  const es = new EventSource(url)
  es.onmessage = (m: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(m.data) as ChatEvent)
    } catch {
      /* ignore a malformed frame; the next reconnect resumes from the last good seq */
    }
  }
  return () => es.close()
}
