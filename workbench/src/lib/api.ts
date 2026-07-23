// Thin client for the engine's session backend. Everything goes through the same-origin
// `/api` proxy (see vite.config.ts) so fetch + EventSource never cross an origin.
import type {
  CatalogItem,
  SessionEvent,
  SessionListItem,
  SessionState,
  Snapshot,
  Subject,
} from "../types";

const BASE = "/api";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export async function getCatalog(): Promise<CatalogItem[]> {
  const data = await json<{ incidents: CatalogItem[] }>(await fetchSafe(`${BASE}/catalog`));
  return data.incidents;
}

export async function createSession(subject: Subject): Promise<{
  session_id: string;
  state: SessionState;
  snapshot: Snapshot;
}> {
  return json(
    await fetchSafe(`${BASE}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject }),
    })
  );
}

export async function getSnapshot(id: string): Promise<Snapshot> {
  return json(await fetchSafe(`${BASE}/sessions/${encodeURIComponent(id)}`));
}

export async function listSessions(): Promise<SessionListItem[]> {
  const data = await json<{ sessions: SessionListItem[] }>(await fetchSafe(`${BASE}/sessions`));
  return data.sessions;
}

export type GateDecision = "approve" | "refine" | "deny";

export async function decideGate(
  id: string,
  decision: GateDecision,
  opts: { params?: Record<string, unknown>; reason?: string } = {}
): Promise<{ events: SessionEvent[]; state: SessionState }> {
  return json(
    await fetchSafe(`${BASE}/sessions/${encodeURIComponent(id)}/gate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, params: opts.params ?? null, reason: opts.reason ?? "" }),
    })
  );
}

export async function advance(id: string): Promise<{ events: SessionEvent[]; state: SessionState }> {
  return json(
    await fetchSafe(`${BASE}/sessions/${encodeURIComponent(id)}/advance`, { method: "POST" })
  );
}

/** Answer an open phase-review (owner 2026-07-23): approve (advance) · refine (re-run the phase
 *  with `text` as a steer) · deny (halt). Parallels decideGate but on the DIRECTION, not a write. */
export async function decideReview(
  id: string,
  decision: GateDecision,
  opts: { text?: string } = {}
): Promise<{ events: SessionEvent[]; state: SessionState }> {
  return json(
    await fetchSafe(`${BASE}/sessions/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, text: opts.text ?? "" }),
    })
  );
}

/** Send an operator chat turn — steering while running, an answer while suspended (obs 2). */
export async function sendMessage(id: string, text: string): Promise<{ message: unknown }> {
  return json(
    await fetchSafe(`${BASE}/sessions/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    })
  );
}

/** URL for the resumable SSE stream — pass the last seq already applied. */
export function streamUrl(id: string, after: number): string {
  return `${BASE}/sessions/${encodeURIComponent(id)}/stream?after=${after}`;
}

// A `fetch` wrapper that surfaces a friendlier message when the backend is unreachable.
async function fetchSafe(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    throw new Error(
      `Cannot reach the investigation engine at ${input}. Is the backend running on :8099? ` +
        `(${err instanceof Error ? err.message : String(err)})`
    );
  }
}
