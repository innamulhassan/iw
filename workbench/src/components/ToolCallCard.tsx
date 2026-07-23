import { useState } from "react";
import type { ToolCall } from "../lib/store";
import { servedIntentPurpose } from "../lib/labels";

// A capability call shown as a compact agent-trace card (obs: "say what the query IN is and what
// came OUT"): the tool + a one-line RESULT collapsed, expandable to WHY it ran, the QUERY it sent,
// the RESULT it folded, and the trace span (kind · when · duration).

// what each tool is for — the "why", in plain words (falls back to the intent name)
const PURPOSE: Record<string, string> = {
  get_incident: "pull the incident record",
  ingest_alert: "read the firing alert",
  active_alerts: "read the firing alerts",
  find_recent_changes: "find recent changes on the affected asset",
  query_change_log: "search the change log",
  get_dependencies: "map the declared dependencies",
  seed_graph: "seed the topology from the CMDB",
  impact_analysis: "compute the impact / blast radius",
  assess_impact: "assess the impacted services",
  get_ci: "read the configuration item",
  list_related_incidents: "find related / co-firing incidents",
  get_correlated_incident: "get the correlated incident (event aggregation)",
  list_correlated_alerts: "list the correlated alert cluster",
  get_flapping_signals: "read the flapping / co-firing signals",
  fetch_metrics: "query the RED / USE metrics",
  instant_query: "run a point-in-time metric query",
  range_query: "query a metric over the incident window",
  get_snapshots: "read APM snapshots / exit-calls",
  fetch_traces: "pull distributed traces",
  bt_health: "read business-transaction health",
  flowmap: "read the service flow map",
  healthrule_violations: "read health-rule violations",
  diff_range: "inspect the change's diff",
  read_diff: "read the diff",
  blame: "blame the offending line",
  get_commit: "read the commit",
  get_pr_for_commit: "read the pull request",
  rollout_status: "read the rollout status",
  pod_status: "read pod status",
  events: "read platform events",
  pod_logs: "read the pod logs",
  search_errors: "search error logs",
  fetch_logs: "fetch logs",
  error_signature_topk: "read the top error signatures",
  search_fw_denies: "search firewall denials",
  transaction_trace: "read a transaction trace",
  propose_fix: "propose the fix",
  apply_mitigation: "apply the proposed mitigation",
  apply_remediation: "apply the proposed remediation",
};

function fmtDuration(ms?: number | null): string | null {
  if (ms == null) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}
function fmtClock(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** The boundary-honesty outcome (P3 step 1): the engine distinguishes data · empty (an HONEST
 *  no-data read) · error (a FAILED call — carries NO evidentiary weight, never "no data") ·
 *  blocked. Older recorded streams may lack the field — fall back to blocked-or-data so a
 *  legacy card never claims an honesty level the engine didn't assert. */
function outcomeOf(call: ToolCall): string {
  if (call.outcome) return call.outcome;
  return call.blocked ? "blocked" : "data";
}

const OUTCOME_ICON: Record<string, string> = {
  blocked: "⛔",
  error: "⚠️",
  empty: "∅",
};

function outText(call: ToolCall, outcome: string): string {
  switch (outcome) {
    case "blocked":
      return `blocked — ${call.reason ?? "no approved gate"}`;
    case "error":
      // error-honesty made visible: a failed call is NOT negative evidence
      return `call failed — ${call.reason ?? "provider error"} · no evidence`;
    case "empty":
      return "no data — clean empty (the provider answered; nothing to fold)";
    default:
      return call.summary || `${call.op_count} ops`;
  }
}

export default function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const isWrite = call.effect === "write";
  const outcome = outcomeOf(call);
  const dur = fmtDuration(call.durationMs);
  const started = fmtClock(call.startedAt);
  const kind = call.kind ?? (isWrite ? "workflow" : "tool");
  // M25 layering: curated per-intent purpose → engine-served capability purpose → de-underscored
  const purpose = PURPOSE[call.intent] ?? servedIntentPurpose(call.intent) ?? call.intent.replace(/_/g, " ");
  const paramEntries = Object.entries(call.params ?? {});
  const out = outText(call, outcome);

  return (
    <div className={`toolcall toolcall--${outcome} ${isWrite ? "toolcall--write" : ""}`}>
      <button className="toolcall__summary" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`toolcall__chevron ${open ? "is-open" : ""}`}>▶</span>
        <span className="toolcall__icon" aria-hidden="true">
          {OUTCOME_ICON[outcome] ?? (isWrite ? "✍️" : "🔧")}
        </span>
        <code className="toolcall__intent">{call.intent}</code>
        <span className="toolcall__provider">{call.provider}</span>
        {call.servedBy && (
          <span
            className={`toolcall__transport toolcall__transport--${call.servedBy}`}
            title={`served via ${call.servedBy}${call.binding ? ` · ${call.binding} binding` : ""} — the transport that fetched this (mock vs live)`}
          >
            📡 {call.servedBy}
            {call.binding ? ` · ${call.binding}` : ""}
          </span>
        )}
        <span className={`toolcall__kind toolcall__kind--${kind}`}>{kind}</span>
        {outcome !== "data" && (
          <span className={`toolcall__outcome toolcall__outcome--${outcome}`}>{outcome}</span>
        )}
        <span className="toolcall__result">
          → {out}
          {dur && <span className="toolcall__dur"> · {dur}</span>}
        </span>
      </button>
      {open && (
        <div className="toolcall__trace">
          <div className="tr-row">
            <span className="tr-k">why</span>
            <span className="tr-v">{purpose}</span>
          </div>
          <div className="tr-row">
            <span className="tr-k">in</span>
            <span className="tr-v">
              {paramEntries.length ? (
                paramEntries.map(([k, v]) => (
                  <span key={k} className="tr-param">
                    <b>{k}</b>: {fmtVal(v)}
                  </span>
                ))
              ) : (
                <span className="tr-muted">{call.provider}.{call.intent}()</span>
              )}
            </span>
          </div>
          <div className="tr-row">
            <span className="tr-k">out</span>
            <span className={`tr-v ${outcome === "blocked" || outcome === "error" ? "tr-blocked" : ""}`}>
              {out}
            </span>
          </div>
          <div className="tr-row">
            <span className="tr-k">outcome</span>
            <span className="tr-v tr-muted">
              {outcome === "data" && "data — ops folded into the graph"}
              {outcome === "empty" && "clean-empty — an honest no-data read (refuting weight allowed)"}
              {outcome === "error" && "error — the call failed; carries NO evidentiary weight"}
              {outcome === "blocked" && "blocked — the write had no approved gate"}
              {!["data", "empty", "error", "blocked"].includes(outcome) && outcome}
            </span>
          </div>
          {call.servedBy && (
            <div className="tr-row">
              <span className="tr-k">via</span>
              <span className="tr-v tr-muted">
                {call.servedBy}
                {call.binding ? ` · ${call.binding} binding` : ""}
                <span className="tr-muted"> — the transport that served this call (mock vs live)</span>
              </span>
            </div>
          )}
          <div className="tr-row">
            <span className="tr-k">trace</span>
            <span className="tr-v tr-muted">
              {kind}
              {started ? ` · ${started}` : ""}
              {dur ? ` · ${dur}` : ""}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
