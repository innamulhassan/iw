// The engine-served label dictionary (M25) — a small app-global singleton so the UI's pure label
// helpers (format.ts, LiveGraph, ToolCallCard) stop being the sole author of the vocabulary. The
// engine serves predicate/relation/intent labels on the snapshot; `setServedDictionary` captures it
// once on cold-load. The lookups return `undefined` until a snapshot lands (and for any vocab the
// engine didn't serve), so every caller keeps its curated map as the OVERRIDE and a de-underscored
// raw string as the final fallback — behavior-preserving, with the served map filling gaps
// (drift-prevention: a NEW engine predicate/edge/intent is labelled without a UI edit).
import type { InvestigationDictionary } from "../types";

let served: InvestigationDictionary | null = null;

/** Capture the engine's served label dictionary (called from the cold-load seed). Ignores a missing
 *  dictionary (an older snapshot) so the local curated labels keep working unchanged. */
export function setServedDictionary(dict?: InvestigationDictionary | null): void {
  if (dict) served = dict;
}

/** Test/reset hook — clear the captured dictionary. */
export function resetServedDictionary(): void {
  served = null;
}

export function servedPredicateLabel(predicate: string): string | undefined {
  return served?.predicates?.[predicate];
}

export function servedRelationLabel(edgeType: string): string | undefined {
  return served?.relations?.[edgeType];
}

export function servedIntentPurpose(intent: string): string | undefined {
  return served?.intents?.[intent];
}
