import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { SessionEvent, Subject } from "../types";
import { advance, createSession, decideGate, getSnapshot, sendMessage, streamUrl } from "./api";
import type { GateDecision } from "./api";
import { emptyState, reduce } from "./store";

const EVENT_TYPES: SessionEvent["type"][] = [
  "phase_started",
  "reasoning",
  "capability_call",
  "graph_delta",
  "ledger_delta",
  "gate_opened",
  "gate_decision",
  "user_message",
  "session_error",
  "session_state",
];

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

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  // full resync from the authoritative snapshot: apply any events we missed (dedup by seq →
  // builds late turns + sets state), then merge full node props / ledger statements.
  const reconcile = useCallback(async (id: string) => {
    try {
      const snap = await getSnapshot(id);
      dispatch({ kind: "events", events: snap.events });
      dispatch({ kind: "mergeDetail", snapshot: snap });
    } catch {
      /* best-effort — the live deltas already carry ids + badges */
    }
  }, []);

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
      es.onerror = () => {
        // the stream naturally ends (server closes) once the session is CLOSED; only surface a
        // persistent failure while we still expect events.
        if (es.readyState === EventSource.CLOSED && esRef.current === es) esRef.current = null;
      };
      esRef.current = es;
    },
    [closeStream, reconcile]
  );

  const load = useCallback(
    async (id: string, snapshotSeq: number) => {
      idRef.current = id;
      subscribe(id, snapshotSeq);
    },
    [subscribe]
  );

  /** Start a fresh investigation from a subject (POST /sessions). */
  const open = useCallback(
    async (subject: Subject) => {
      setBusy(true);
      setError(null);
      answeredGates.current.clear();
      dispatch({ kind: "reset" });
      try {
        const res = await createSession(subject);
        dispatch({ kind: "seed", snapshot: res.snapshot });
        await load(res.session_id, res.snapshot.events.at(-1)?.seq ?? 0);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [load]
  );

  /** Open an existing (possibly CLOSED) investigation by id — cold-load its full state. */
  const openExisting = useCallback(
    async (id: string) => {
      setBusy(true);
      setError(null);
      answeredGates.current.clear();
      dispatch({ kind: "reset" });
      try {
        const snap = await getSnapshot(id);
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

  /** Manually step to the next pause (used only if a run pauses without a gate). */
  const step = useCallback(async () => {
    const id = idRef.current;
    if (!id) return;
    setBusy(true);
    try {
      const res = await advance(id);
      dispatch({ kind: "events", events: res.events });
      await reconcile(id);
    } finally {
      setBusy(false);
    }
  }, [reconcile]);

  const reset = useCallback(() => {
    closeStream();
    idRef.current = null;
    answeredGates.current.clear();
    dispatch({ kind: "reset" });
    setError(null);
  }, [closeStream]);

  useEffect(() => () => closeStream(), [closeStream]);

  return { state, error, busy, open, openExisting, decide, send, step, reset };
}
