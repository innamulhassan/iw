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

// The DECLARED transport a LIVE call would use — the adapter's Binding, upper-cased for the badge.
// (This is the protocol a real fetch dispatches on; the mock IGNORES it — see MOCK_HONESTY.)
const PROTOCOL: Record<string, string> = { mcp: "MCP", rest: "REST", a2a: "A2A" };
function protocolLabel(binding?: string | null): string | null {
  if (!binding) return null;
  return PROTOCOL[binding] ?? binding.toUpperCase();
}
// The owner's honesty line: a mock does NOT speak MCP/REST/A2A — it mimes the tool's shape. The
// badge must say "simulates", never "mock · mcp" (which reads as 'the mock uses mcp').
const MOCK_HONESTY =
  "the mock test transport mimes the tool's shape — no real MCP/REST/A2A call is made; " +
  "the protocol shown is the binding a live call would use.";
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
  // JOURNAL story fidelity: a reasoned step's ops are planner-authored, so the mock TRANSPORT
  // outcome reads "empty" (no fixture) even though the step produced real findings. Prefer the
  // ATTRIBUTED result/produced over the transport outcome, so a step that found something renders
  // as "data", not "no data — clean empty". A genuinely empty read carries neither, so it is
  // untouched — the honesty boundary is preserved.
  if ((call.produced && call.produced.length > 0) || (call.result && call.result.trim())) return "data";
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
      // the reasoned-step RESULT (what came back, in words) leads; then the live one-liner; the
      // raw op count is the last resort — never the whole story.
      return call.result || call.summary || `${call.op_count} ops`;
  }
}

export default function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const isWrite = call.effect === "write";
  const outcome = outcomeOf(call);
  // HONESTY (owner): a mock never really transported anything, so it has no genuine wall-clock
  // duration — show "simulated · instant", never a misleading "0ms". A live call keeps its measured
  // span. The DECLARED protocol (binding) is shown regardless; only the served-by label distinguishes
  // "this MIMED MCP" (mock) from "this DID call over MCP" (live).
  const isMock = call.servedBy === "mock";
  const protocol = protocolLabel(call.binding);
  const dur = fmtDuration(call.durationMs);
  const timing = isMock ? "simulated · instant" : dur; // never "0ms" for a mock
  const started = fmtClock(call.startedAt);
  const callable = `${call.provider}.${call.intent}`; // the provider-qualified CALLABLE invoked
  const kind = call.kind ?? (isWrite ? "workflow" : "tool");
  // The WHY, in priority order: the planner's OWN per-call rationale (the real reasoning it
  // authored) → curated per-intent purpose → engine-served capability purpose → de-underscored raw.
  // The hardcoded purpose is only a fallback when no reasoning exists — never over the real why.
  const purpose =
    call.rationale ?? PURPOSE[call.intent] ?? servedIntentPurpose(call.intent) ?? call.intent.replace(/_/g, " ");
  const paramEntries = Object.entries(call.params ?? {});
  const out = outText(call, outcome);

  return (
    <div className={`toolcall toolcall--${outcome} ${isWrite ? "toolcall--write" : ""}`}>
      <button className="toolcall__summary" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`toolcall__chevron ${open ? "is-open" : ""}`}>▶</span>
        <span className="toolcall__icon" aria-hidden="true">
          {OUTCOME_ICON[outcome] ?? (isWrite ? "✍️" : "🔧")}
        </span>
        <code className="toolcall__intent">{callable}</code>
        {protocol && (
          <span
            className="toolcall__protocol"
            title={`declared transport — a live call would use ${protocol}`}
          >
            {protocol}
          </span>
        )}
        {call.servedBy &&
          (isMock ? (
            // the mock MIMES the protocol — say so, never "mock · mcp" (owner's core honesty point)
            <span className="toolcall__transport toolcall__transport--mock" title={MOCK_HONESTY}>
              MOCK{protocol ? ` · simulates ${protocol}` : " · simulated"}
            </span>
          ) : (
            // a live transport DID speak the protocol — show it plainly
            <span
              className={`toolcall__transport toolcall__transport--${call.servedBy}`}
              title={`served live via ${call.servedBy}${protocol ? ` — a real ${protocol} call was made` : ""}`}
            >
              📡 {call.servedBy}
            </span>
          ))}
        <span className={`toolcall__kind toolcall__kind--${kind}`}>{kind}</span>
        {outcome !== "data" && (
          <span className={`toolcall__outcome toolcall__outcome--${outcome}`}>{outcome}</span>
        )}
        <span className="toolcall__result">
          → {out}
          {/* only a LIVE call shows a measured span here; the mock's honesty rides its badge + trace */}
          {!isMock && dur && <span className="toolcall__dur"> · {dur}</span>}
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
          {/* the ops this call FOLDED into the graph — the reasoned step's evidence, itemized */}
          {call.produced && call.produced.length > 0 && (
            <div className="tr-row">
              <span className="tr-k">made</span>
              <span className="tr-v">
                <span className="tr-produced">
                  {call.produced.map((p, i) => (
                    <code key={`${p}-${i}`} className="tr-prod">{p}</code>
                  ))}
                </span>
              </span>
            </div>
          )}
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
                {isMock ? (
                  <>
                    MOCK{protocol ? ` — simulates ${protocol}` : ""}: the mock test transport mimes the
                    tool's shape; no real {protocol ?? "MCP/REST/A2A"} call is made
                    {protocol ? ` (${protocol} is the binding a live call would use)` : ""}.
                  </>
                ) : (
                  <>
                    {call.servedBy}
                    {protocol ? ` · a real ${protocol} call served this` : " — live transport"}
                  </>
                )}
              </span>
            </div>
          )}
          <div className="tr-row">
            <span className="tr-k">trace</span>
            <span className="tr-v tr-muted">
              {kind}
              {/* the invocation TIMESTAMP always; then the live span, or "simulated · instant" for a mock */}
              {started ? ` · ${started}` : ""}
              {timing ? ` · ${timing}` : ""}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
