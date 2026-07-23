import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { SessionEvent, SessionState, Subject } from "../types";
import { createSession, decideGate, decideReview, getSnapshot, sendMessage, streamUrl } from "./api";
import type { GateDecision } from "./api";
import { setServedDictionary } from "./labels";
import { emptyState, reduce } from "./store";

const EVENT_TYPES: SessionEvent["type"][] = [
  "phase_started",
  "reasoning",
  "capability_call",
  "graph_delta",
  "hypotheses_delta",
  "gate_opened",
  "gate_decision",
  "phase_review_opened",
  "phase_review_decision",
  "user_message",
  "session_error",
  "session_state",
];

/** Backoff schedule for SSE re-subscribes: 1s, 2s, then 5s (capped) per attempt. */
const RECONNECT_DELAYS_MS = [1000, 2000, 5000];
/** Consecutive failed (re)connects tolerated before giving up with a visible error. */
const MAX_RECONNECT_ATTEMPTS = 10;

/** Owns ONE live investigation: cold-loads its snapshot, subscribes to the resumable SSE
 *  stream, folds every delta through the store reducer, and exposes the gate-decision action.
 *  The engine drives node-expansion; the human only answers the write-gate. */
export function useInvestigation() {
  const [state, dispatch] = useReducer(reduce, undefined, emptyState);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const idRef = useRef<string | null>(null);
  const answeredGates = useRef<Set<string>>(new Set()); // gate ids already submitted (dedupe)
  const answeredReviews = useRef<Set<string>>(new Set()); // review ids already submitted (dedupe)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0); // consecutive failed (re)connects since the last healthy open
  const lastSeqRef = useRef(0); // latest APPLIED seq — the resume cursor for reconnects
  const sessionStateRef = useRef<SessionState | null>(null);

  // Mirror the reducer's cursor + liveness into refs so the stable subscribe/reconnect
  // callbacks always see the CURRENT values, not the ones captured at subscribe time.
  useEffect(() => {
    lastSeqRef.current = state.lastSeq;
    sessionStateRef.current = state.state;
  });

  const closeStream = useCallback(() => {
    if (reconnectTimer.current !== null) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    esRef.current?.close();
    esRef.current = null;
  }, []);

  // full resync from the authoritative snapshot: apply any events we missed (dedup by seq →
  // builds late turns + sets state), then merge full node props / hypotheses statements.
  const reconcile = useCallback(async (id: string) => {
    try {
      const snap = await getSnapshot(id);
      dispatch({ kind: "events", events: snap.events });
      dispatch({ kind: "mergeDetail", snapshot: snap });
    } catch {
      /* best-effort — the live deltas already carry ids + badges */
    }
  }, []);

  // Self-reference so the reconnect timer can re-subscribe through the stable callback.
  const subscribeRef = useRef<(id: string, after: number) => void>(() => {});

  const subscribe = useCallback(
    (id: string, after: number) => {
      closeStream();
      const es = new EventSource(streamUrl(id, after));
      const onEvent = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data) as SessionEvent;
          dispatch({ kind: "events", events: [parsed] });
          if (parsed.type === "session_state" && parsed.state !== "running") {
            void reconcile(id);
          }
        } catch {
          /* ignore malformed frame */
        }
      };
      for (const t of EVENT_TYPES) es.addEventListener(t, onEvent as EventListener);
      es.addEventListener("closed", () => closeStream());
      es.onopen = () => {
        reconnectAttempts.current = 0; // healthy again — a later drop starts backoff fresh
      };
      es.onerror = () => {
        // EventSource auto-retries only from CONNECTING; CLOSED is terminal (e.g. the /api
        // proxy answered 502 while the backend restarts, or the stream endpoint 404'd). The
        // stream also naturally ends once the session is CLOSED — never reconnect then. While
        // the session is still live, re-subscribe with capped exponential backoff, resuming
        // from the LATEST applied seq — otherwise the investigation silently freezes with no
        // error and no recovery short of a full reload.
        if (es.readyState !== EventSource.CLOSED || esRef.current !== es) return;
        es.close();
        esRef.current = null;
        if (sessionStateRef.current === "closed") return; // nothing more to stream
        if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
          setError(
            `Live stream lost — gave up after ${MAX_RECONNECT_ATTEMPTS} reconnect attempts. Reload to resume.`
          );
          return;
        }
        const delay =
          RECONNECT_DELAYS_MS[Math.min(reconnectAttempts.current, RECONNECT_DELAYS_MS.length - 1)];
        reconnectAttempts.current += 1;
        reconnectTimer.current = setTimeout(() => {
          reconnectTimer.current = null;
          if (idRef.current !== id || sessionStateRef.current === "closed") return; // superseded
          subscribeRef.current(id, lastSeqRef.current); // resume from the true cursor
        }, delay);
      };
      esRef.current = es;
    },
    [closeStream, reconcile]
  );

  useEffect(() => {
    subscribeRef.current = subscribe;
  }, [subscribe]);

  const load = useCallback(
    async (id: string, snapshotSeq: number) => {
      idRef.current = id;
      reconnectAttempts.current = 0; // a fresh cold-load resets the backoff budget
      subscribe(id, snapshotSeq);
    },
    [subscribe]
  );

  /** Start a fresh investigation from a subject (POST /sessions). */
  const open = useCallback(
    async (subject: Subject) => {
      setBusy(true);
      setError(null);
      closeStream(); // drop the previous stream + any pending reconnect before reseeding
      answeredGates.current.clear();
      answeredReviews.current.clear();
      dispatch({ kind: "reset" });
      try {
        const res = await createSession(subject);
        setServedDictionary(res.snapshot.dictionary); // capture the engine's label vocab (M25)
        dispatch({ kind: "seed", snapshot: res.snapshot });
        await load(res.session_id, res.snapshot.events.at(-1)?.seq ?? 0);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [closeStream, load]
  );

  /** Open an existing (possibly CLOSED) investigation by id — cold-load its full state. */
  const openExisting = useCallback(
    async (id: string) => {
      setBusy(true);
      setError(null);
      closeStream(); // drop the previous stream + any pending reconnect before reseeding
      answeredGates.current.clear();
      answeredReviews.current.clear();
      dispatch({ kind: "reset" });
      try {
        const snap = await getSnapshot(id);
        setServedDictionary(snap.dictionary); // capture the engine's label vocab (M25)
        dispatch({ kind: "seed", snapshot: snap });
        if (snap.state === "closed") {
          closeStream(); // nothing more to stream
        } else {
          await load(id, snap.events.at(-1)?.seq ?? 0);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [closeStream, load]
  );

  /** Answer the open write-gate: approve | refine (params) | deny (reason). */
  const decide = useCallback(
    async (gateId: string, decision: GateDecision, opts: { params?: Record<string, unknown>; reason?: string } = {}) => {
      const id = idRef.current;
      if (!id || answeredGates.current.has(gateId)) return; // answer each gate exactly once
      answeredGates.current.add(gateId);
      dispatch({ kind: "decision", gateId, decision, reason: opts.reason });
      setBusy(true);
      try {
        const res = await decideGate(id, decision, opts);
        dispatch({ kind: "events", events: res.events });
        await reconcile(id);
      } catch (err) {
        // a 409 means the backend already advanced past this gate — self-heal by reconciling
        if (err instanceof Error && err.message.startsWith("409")) {
          await reconcile(id);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setBusy(false);
      }
    },
    [reconcile]
  );

  /** Answer the open phase-review: approve (advance) | refine (re-run the phase with a steer) |
   *  deny (halt). The DIRECTION counterpart to `decide` — same optimistic + dedupe + 409-self-heal
   *  round-trip, but through POST /review, and the summary card carries no write params. */
  const review = useCallback(
    async (reviewId: string, decision: GateDecision, opts: { text?: string } = {}) => {
      const id = idRef.current;
      if (!id || answeredReviews.current.has(reviewId)) return; // answer each review exactly once
      answeredReviews.current.add(reviewId);
      dispatch({ kind: "reviewDecision", reviewId, decision, reason: opts.text });
      setBusy(true);
      try {
        const res = await decideReview(id, decision, opts);
        dispatch({ kind: "events", events: res.events });
        await reconcile(id);
      } catch (err) {
        // a 409 means the backend already advanced past this review — self-heal by reconciling
        if (err instanceof Error && err.message.startsWith("409")) {
          await reconcile(id);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setBusy(false);
      }
    },
    [reconcile]
  );

  /** Send an operator chat turn (obs 2) — steering / adding context / answering. The engine
   *  buffers it and the LIVE planner reads it on its next plan; the resulting `user_message`
   *  event streams back over SSE (reconcile is a belt-and-suspenders for the closed stream). */
  const send = useCallback(
    async (text: string) => {
      const id = idRef.current;
      if (!id || !text.trim()) return;
      try {
        await sendMessage(id, text.trim());
        await reconcile(id);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reconcile]
  );

  const reset = useCallback(() => {
    closeStream();
    idRef.current = null;
    reconnectAttempts.current = 0;
    answeredGates.current.clear();
      answeredReviews.current.clear();
    dispatch({ kind: "reset" });
    setError(null);
  }, [closeStream]);

  useEffect(() => () => closeStream(), [closeStream]);

  return { state, error, busy, open, openExisting, decide, review, send, reset };
}
