import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { LiveState, LiveNode, Selection } from "../lib/store";
import { nodesWithOrder, relatedIncidents } from "../lib/store";
import type { GraphFact, GraphSpan } from "../types";
import { humanizePredicate } from "../lib/format";
import { servedIdentityKeys, servedRelationLabel, servedSpeciesForPredicate } from "../lib/labels";
import { TIER_LABELS, TIER_ORDER, layerLabelForType, tierForType } from "../lib/tiers";
import PanelControls from "./PanelControls";
import type { PanelControlState } from "./PanelControls";

const NODE_W = 168;
const NODE_H = 66;
const COL_GAP = 230;
const ROW_GAP = 96;
const MARGIN_X = 70;
const MARGIN_Y = 60;
const FIT_PAD = 26;

const PREFERRED_LABEL_KEYS = [
  "service_name",
  "incident_id",
  "alert_id",
  "change_id",
  "db_id",
  "sha",
  "signature_hash",
  "anomaly_id",
  "segment_id",
  "rule_id",
  "uid",
  "release_id",
  "statement",
];

function shortId(id: string): string {
  const idx = id.indexOf(":");
  return idx >= 0 ? id.slice(idx + 1) : id;
}

function labelForNode(node: LiveNode): string {
  const props = node.props ?? {};
  for (const key of PREFERRED_LABEL_KEYS) {
    const value = props[key];
    if (value === undefined || value === null) continue;
    let text = String(value);
    if (key === "sha") text = text.slice(0, 7);
    return text.length > 22 ? `${text.slice(0, 21)}…` : text;
  }
  return shortId(node.id).slice(0, 22);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// ── the six datum-shape categories (2026-07-23 primitives §2) ────────────────────────────────
// A node carries datums of up to six shapes; the node-detail groups them so a human reads identity
// vs a state-trail vs a reading vs a bounded span at a glance. A FACT is bucketed by the engine's
// served species for its CANONICAL predicate (reading|property|state); events + spans are their own
// bundle collections. Unknown/uncataloged predicates fall to STATE (the §9.1 "when in doubt" default).
type FactCategory = "reading" | "property" | "state";
function factCategory(predicate: string): FactCategory {
  const s = servedSpeciesForPredicate(predicate);
  if (s === "reading") return "reading";
  if (s === "property") return "property";
  return "state"; // state, or an uncataloged predicate → the cheap-direction default
}

/** A SPAN's duration, humanized (ms → s → min). `null` ended_at = still open / lost close. */
function spanDuration(s: GraphSpan): string | null {
  if (!s.ended_at) return null;
  const ms = new Date(s.ended_at).getTime() - new Date(s.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
  return `${Math.round(ms / 60000)} min`;
}

function clockTime(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Group a subject's STATE facts into per-predicate TRAILS (2026-07-23 §2.3): one row per attribute
 *  name, its held values ordered by valid_from, the current (open) value last. A single-value trail
 *  is just a state; a multi-value trail is the supersede chain (status up→down, version a→b). */
interface StateTrail {
  predicate: string;
  steps: GraphFact[]; // ordered oldest → newest; the last open one is "current"
}
function stateTrails(stateFacts: GraphFact[]): StateTrail[] {
  const byPred = new Map<string, GraphFact[]>();
  for (const f of stateFacts) {
    const list = byPred.get(f.predicate);
    if (list) list.push(f);
    else byPred.set(f.predicate, [f]);
  }
  return [...byPred.entries()]
    .map(([predicate, steps]) => ({
      predicate,
      steps: steps.slice().sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime()),
    }))
    .sort((a, b) => a.predicate.localeCompare(b.predicate));
}

/** Parse the incident `work_notes` prop into an ordered JOURNAL (2026-07-23 §3 ANNOTATION / the
 *  LOG→promoted trail): each independently-timestamped human note is one entry. Splits on newlines
 *  and lifts a leading `[HH:MM] actor:` stamp when present, so the terse single-line twins and the
 *  multi-line timestamped live records both read as a journal. */
interface WorkNote {
  stamp: string | null; // the note's time — a formatted `at`, or a lifted [HH:MM] prefix
  author: string | null; // who logged it (the structured LOG shape carries it; a flat line has none)
  text: string;
}
function parseWorkNotes(raw: unknown): WorkNote[] {
  // The forward-compatible LOG shape (§3 ANNOTATION): an append-only array of independently-
  // timestamped notes, each `{ at, author, text }`, rendered as a timestamped work-log. work_notes
  // may still arrive as a flat STRING (a terse twin / a legacy field) — split it into lines, lifting
  // a leading `[HH:MM]` stamp when present — so both shapes render through the same journal.
  if (Array.isArray(raw)) {
    return raw
      .map((n): WorkNote | null => {
        if (n && typeof n === "object") {
          const o = n as { at?: unknown; author?: unknown; text?: unknown };
          const text = typeof o.text === "string" ? o.text : "";
          if (!text.trim()) return null;
          const at = typeof o.at === "string" ? o.at : null;
          return {
            stamp: (at && clockTime(at)) || at,
            author: typeof o.author === "string" ? o.author : null,
            text: text.trim(),
          };
        }
        return typeof n === "string" && n.trim() ? { stamp: null, author: null, text: n.trim() } : null;
      })
      .filter((n): n is WorkNote => n !== null);
  }
  if (typeof raw !== "string" || !raw.trim()) return [];
  return raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const m = line.match(/^\[([^\]]+)\]\s*(.*)$/);
      return m ? { stamp: m[1], author: null, text: m[2] } : { stamp: null, author: null, text: line };
    });
}

function relTime(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const EDGE_COLORS: Record<string, string> = {
  caused_by: "#a78bfa",
  supports: "#4ade80",
  refutes: "#f87171",
  correlated_with: "#fbbf24",
  similar_to: "#2dd4bf",
  recurrence_of: "#2dd4bf",
};
const STRUCTURAL_COLOR = "#64748b";
const RELATED_EDGE_TYPES = new Set(["similar_to", "recurrence_of"]);

function colorForEdge(type: string, origin: string): string {
  if (EDGE_COLORS[type]) return EDGE_COLORS[type];
  if (origin === "inferred") return "#a78bfa";
  return STRUCTURAL_COLOR;
}

const RELATION_LABELS: Record<string, string> = {
  depends_on: "depends on",
  calls: "calls",
  runs_on: "runs on",
  hosted_on: "hosted on",
  deployed_to: "deployed to",
  contains: "contains",
  exposes: "exposes",
  routes_to: "routes to",
  connects_to: "connects to",
  reads_from: "reads from",
  writes_to: "writes to",
  produces_to: "produces to",
  consumes_from: "consumes from",
  secured_by: "secured by",
  changed_by: "changed by",
  caused_by: "caused by",
  supports: "supports",
  refutes: "refutes",
  correlated_with: "correlated with",
  similar_to: "similar to",
  recurrence_of: "recurrence of",
  affects: "affects",
  fired_on: "fired on",
  introduced_by: "introduced by",
};
function humanizeRelation(t: string): string {
  // M25 layering: curated override → engine-served relation label → de-underscored fallback
  return RELATION_LABELS[t] ?? servedRelationLabel(t) ?? t.replace(/_/g, " ");
}

interface Props {
  live: LiveState;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  /** The shared MAXIMIZE / MINIMIZE controls (Workbench owns the layout state). Optional so the
   *  graph renders standalone in tests without the workbench chrome. */
  panel?: PanelControlState;
}

interface View {
  tx: number;
  ty: number;
  scale: number;
}

// The interactive graph (UI-SPEC §4 / obs 1,4,5,8): nodes laid out by ARCHITECTURAL LAYER lanes
// (Case → Signal → Service → Messaging → Database → Infra → Network → Change), each badged with
// its DENSE creation-order number (#1 = the ServiceNow incident ORIGIN), its LAYER, and WHERE it
// was fetched from (source). Zoom + pan + fit; the hypotheses and graph share one selection so
// clicking a fact/hypothesis cross-highlights the node + fact here. The ENGINE drives growth.
export default function LiveGraph({ live, selection, onSelect, panel }: Props) {
  const [view, setView] = useState<View>({ tx: 0, ty: 0, scale: 1 });
  const dragRef = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const interactedRef = useRef(false); // once the user pans/zooms, stop auto-fitting
  // the edge the pointer is over → a floating detail card (relation · direction · source · when)
  const [hoverEdge, setHoverEdge] = useState<{ id: string; x: number; y: number } | null>(null);

  // Memoized on `live`: these run a sort/scan, and — load-bearing — `positions` is memoized on
  // `[ordered]`, so an unstable `ordered` ref would make `positions` change every render and the
  // center-on-selection effect (deps `[selectedId, positions]`) re-fire → setView → re-render in a
  // loop whenever a node is selected. Keying both to `live` makes them stable between engine deltas.
  const ordered = useMemo(() => nodesWithOrder(live), [live]);
  const related = useMemo(() => relatedIncidents(live), [live]);
  const orderFor = useMemo(() => {
    const m = new Map<string, number>();
    ordered.forEach((n) => m.set(n.id, n.order));
    return m;
  }, [ordered]);
  const relatedIds = useMemo(() => new Set(related.map((r) => r.node.id)), [related]);

  // selection (shared with the hypotheses): a node selection highlights that node; an evidence
  // selection highlights the node it points at — its subject if the id is a Fact, or the node
  // itself if the LLM referenced a NODE id in supporting_facts (both happen on the live path).
  const selectedId = selection
    ? selection.kind === "node"
      ? selection.id
      : (live.facts[selection.id]?.subject ?? (live.nodes[selection.id] ? selection.id : null))
    : null;
  // highlight a fact row in the drawer only when the selected evidence id is a real Fact
  const selectedFactId = selection?.kind === "fact" && live.facts[selection.id] ? selection.id : null;

  const { positions, columns, width, height } = useMemo(() => {
    const cols = TIER_ORDER.map((tier) => ({
      tier,
      nodes: ordered.filter((n) => tierForType(n.type) === tier),
    })).filter((c) => c.nodes.length > 0);

    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((col, ci) => {
      const x = MARGIN_X + ci * COL_GAP + NODE_W / 2;
      col.nodes.forEach((node, ri) => {
        pos.set(node.id, { x, y: MARGIN_Y + ri * ROW_GAP + NODE_H / 2 });
      });
    });
    const maxRows = Math.max(1, ...cols.map((c) => c.nodes.length));
    return {
      positions: pos,
      columns: cols,
      width: MARGIN_X * 2 + Math.max(cols.length, 1) * COL_GAP,
      height: MARGIN_Y * 2 + maxRows * ROW_GAP + 16,
    };
  }, [ordered]);

  const usedColors = useMemo(() => {
    const set = new Set<string>([STRUCTURAL_COLOR]);
    Object.values(live.edges).forEach((e) => set.add(colorForEdge(e.type, e.origin)));
    return Array.from(set);
  }, [live.edges]);

  const rootCandidates = useMemo(
    () =>
      new Set(
        Object.values(live.hypotheses)
          .filter((h) => h.status === "confirmed" && h.root_candidate)
          .map((h) => h.root_candidate as string)
      ),
    [live.hypotheses]
  );

  // fit the whole graph into the viewport (UI-SPEC §4 "viewable in full")
  const fitView = useCallback(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return;
    const contentW = width + FIT_PAD * 2;
    const contentH = height + FIT_PAD * 2;
    const scale = Math.min(1.4, Math.max(0.3, Math.min(rect.width / contentW, rect.height / contentH)));
    const tx = (rect.width - width * scale) / 2 + FIT_PAD * scale;
    const ty = (rect.height - height * scale) / 2;
    setView({ tx, ty, scale: Math.round(scale * 1000) / 1000 });
  }, [width, height]);

  // auto-fit while the user hasn't taken over — so the graph stays fully visible as it grows live
  useLayoutEffect(() => {
    if (!interactedRef.current) fitView();
  }, [fitView, ordered.length]);

  // native, non-passive wheel listener so we can preventDefault (zoom the canvas, not the page)
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      interactedRef.current = true;
      const rect = svg.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      setView((v) => {
        const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        const scale = Math.min(2.6, Math.max(0.3, v.scale * factor));
        const k = scale / v.scale;
        return { scale, tx: px - (px - v.tx) * k, ty: py - (py - v.ty) * k };
      });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  // center the selected node when the selection changes (incl. a hypotheses cross-highlight click)
  useEffect(() => {
    if (!selectedId) return;
    const pos = positions.get(selectedId);
    const svg = svgRef.current;
    if (!pos || !svg) return;
    interactedRef.current = true;
    const rect = svg.getBoundingClientRect();
    setView((v) => ({ ...v, tx: rect.width / 2 - pos.x * v.scale, ty: rect.height / 2 - pos.y * v.scale }));
  }, [selectedId, positions]);

  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as Element).closest(".graph-node")) return; // let node clicks through
    dragRef.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false };
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) {
      d.moved = true;
      interactedRef.current = true;
    }
    setView((v) => ({ ...v, tx: d.tx + (e.clientX - d.x), ty: d.ty + (e.clientY - d.y) }));
  };
  const onPointerUp = () => {
    dragRef.current = null;
  };

  const zoomBy = (factor: number) => {
    interactedRef.current = true;
    setView((v) => {
      const scale = Math.min(2.6, Math.max(0.3, v.scale * factor));
      const rect = svgRef.current?.getBoundingClientRect();
      const cx = rect ? rect.width / 2 : width / 2;
      const cy = rect ? rect.height / 2 : height / 2;
      const k = scale / v.scale;
      return { scale, tx: cx - (cx - v.tx) * k, ty: cy - (cy - v.ty) * k };
    });
  };
  const onFit = () => {
    interactedRef.current = false; // resume auto-fit as the graph keeps growing
    fitView();
  };

  const selected = selectedId ? live.nodes[selectedId] : null;
  const selectedFacts = selectedId
    ? Object.values(live.facts).filter((f) => f.subject === selectedId && f.state !== "retracted")
    : [];
  const selectedEvents = selectedId ? Object.values(live.events).filter((ev) => ev.entity === selectedId) : [];
  // the sixth species: SPAN datums whose subject is this node (§2.6) — a captured trace/BT happening.
  const selectedSpans = selectedId
    ? Object.values(live.spans).filter((s) => s.subject === selectedId)
    : [];

  // ── bucket the node's datums into the six categories for node-detail ────────────────────────
  // facts split by the engine's served species; node props split into IDENTITY vs PROPERTY by the
  // served identity_keys; work_notes lifts out into its own journal (the §3 ANNOTATION trail).
  const readingFacts = selectedFacts.filter((f) => factCategory(f.predicate) === "reading");
  const contentFacts = selectedFacts.filter((f) => factCategory(f.predicate) === "property");
  const stateFactList = selectedFacts.filter((f) => factCategory(f.predicate) === "state");
  const trails = stateTrails(stateFactList);
  const idKeys = selected ? new Set(servedIdentityKeys(selected.type)) : new Set<string>();
  const propEntries = selected ? Object.entries(selected.props ?? {}) : [];
  const identityEntries = propEntries.filter(([k]) => idKeys.has(k));
  const propertyEntries = propEntries.filter(([k]) => !idKeys.has(k) && k !== "work_notes");
  const workNotes = selected ? parseWorkNotes(selected.props?.work_notes) : [];

  return (
    <div className="graph-pane">
      <div className="graph-pane__header">
        <h2 className="pane-title">Incident graph</h2>
        <div className="graph-pane__tools">
          <div className="graph-controls">
            <span className="graph-controls__hint">drag to pan · scroll to zoom</span>
            <button className="graph-controls__btn" onClick={() => zoomBy(1 / 1.2)} title="Zoom out">
              −
            </button>
            <span className="graph-controls__zoom">{Math.round(view.scale * 100)}%</span>
            <button className="graph-controls__btn" onClick={() => zoomBy(1.2)} title="Zoom in">
              +
            </button>
            <button className="graph-controls__btn graph-controls__btn--fit" onClick={onFit} title="Fit to view">
              ⤢
            </button>
          </div>
          {panel && <PanelControls label="graph" {...panel} />}
        </div>
      </div>

      <div className="graph-canvas-scroll">
        <svg
          ref={svgRef}
          className="graph-canvas"
          role="img"
          aria-label="Incident causal graph"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        >
          <defs>
            {usedColors.map((color) => (
              <marker
                key={color}
                id={`arrow-${color.replace("#", "")}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth={7}
                markerHeight={7}
                orient="auto-start-reverse"
              >
                <path d="M0,0 L10,5 L0,10 z" fill={color} />
              </marker>
            ))}
          </defs>

          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
            {columns.map((col, ci) => (
              <text
                key={col.tier}
                x={MARGIN_X + ci * COL_GAP + NODE_W / 2}
                y={26}
                className="graph-column-label"
                textAnchor="middle"
              >
                {TIER_LABELS[col.tier]}
              </text>
            ))}

            {Object.values(live.edges).map((edge) => {
              const from = positions.get(edge.src);
              const to = positions.get(edge.dst);
              if (!from || !to) return null;
              const dx = to.x - from.x;
              const dy = to.y - from.y;
              const dist = Math.sqrt(dx * dx + dy * dy) || 1;
              const nx = -dy / dist;
              const ny = dx / dist;
              const sameCol = from.x === to.x;
              const bow = sameCol ? 44 : 16;
              const sign = edge.id.length % 2 === 0 ? 1 : -1;
              const midX = (from.x + to.x) / 2 + nx * bow * sign;
              const midY = (from.y + to.y) / 2 + ny * bow * sign;
              const color = colorForEdge(edge.type, edge.origin);
              const isRelated = RELATED_EDGE_TYPES.has(edge.type);
              const causal = Boolean(EDGE_COLORS[edge.type]) || edge.origin === "inferred";
              const d = `M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`;
              const isHover = hoverEdge?.id === edge.id;
              const isProvisional = Boolean(edge.provisional); // P3 airlock — tentative, reads dim
              return (
                <g key={edge.id}>
                  <path
                    d={d}
                    fill="none"
                    stroke={color}
                    strokeWidth={isHover ? (causal ? 3.5 : 3) : causal ? 2 : 1.5}
                    strokeDasharray={isProvisional ? "2 3" : isRelated ? "2 4" : causal ? "6 4" : undefined}
                    strokeOpacity={isProvisional ? 0.45 : undefined}
                    markerEnd={`url(#arrow-${color.replace("#", "")})`}
                    className={[
                      causal ? "edge edge--causal" : "edge edge--structural",
                      isProvisional ? "edge--provisional" : "",
                    ]
                      .join(" ")
                      .trim()}
                  />
                  {/* wide invisible hit-area so a thin edge is easy to hover for its detail */}
                  <path
                    d={d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                    style={{ cursor: "pointer" }}
                    onMouseEnter={(e) => setHoverEdge({ id: edge.id, x: e.clientX, y: e.clientY })}
                    onMouseMove={(e) => setHoverEdge({ id: edge.id, x: e.clientX, y: e.clientY })}
                    onMouseLeave={() => setHoverEdge((h) => (h?.id === edge.id ? null : h))}
                  />
                </g>
              );
            })}

            {ordered.map((node) => {
              const pos = positions.get(node.id);
              if (!pos) return null;
              const isSymptom = node.type === "anomaly";
              const isOrigin = Boolean(node.origin);
              const isRoot = rootCandidates.has(node.id);
              const isRelated = relatedIds.has(node.id);
              const isSelected = node.id === selectedId;
              const tier = tierForType(node.type);
              return (
                <g
                  key={node.id}
                  transform={`translate(${pos.x - NODE_W / 2}, ${pos.y - NODE_H / 2})`}
                  className={[
                    "graph-node",
                    `graph-node--${tier}`,
                    isSymptom ? "graph-node--symptom" : "",
                    isOrigin ? "graph-node--origin" : "",
                    isRoot ? "graph-node--root" : "",
                    isRelated ? "graph-node--related" : "",
                    isSelected ? "graph-node--selected" : "",
                  ]
                    .join(" ")
                    .trim()}
                  onClick={() => onSelect({ kind: "node", id: node.id })}
                  tabIndex={0}
                  role="button"
                  aria-pressed={isSelected}
                >
                  <rect width={NODE_W} height={NODE_H} rx={10} className="graph-node__rect" />
                  {isOrigin ? (
                    <text x={16} y={-6} className="graph-node__entry-tag">
                      ★ ORIGIN · INCIDENT
                    </text>
                  ) : isSymptom ? (
                    <text x={16} y={-6} className="graph-node__entry-tag">
                      ⭑ SYMPTOM
                    </text>
                  ) : null}
                  {isRelated && (
                    <text x={NODE_W - 6} y={-6} className="graph-node__related-tag" textAnchor="end">
                      ↗ RELATED
                    </text>
                  )}
                  <circle cx={0} cy={NODE_H / 2} r={13} className="graph-node__seq" />
                  <text x={0} y={NODE_H / 2} className="graph-node__seq-num" textAnchor="middle" dominantBaseline="central">
                    {node.order}
                  </text>
                  <text x={16} y={20} className="graph-node__type">
                    {node.type}
                  </text>
                  <text x={NODE_W - 12} y={20} className="graph-node__layer" textAnchor="end">
                    {layerLabelForType(node.type)}
                  </text>
                  <text x={16} y={40} className="graph-node__label">
                    {node.type === "hypothesis" && live.hypotheses[node.id]?.statement
                      ? (live.hypotheses[node.id].statement.length > 22
                          ? `${live.hypotheses[node.id].statement.slice(0, 21)}…`
                          : live.hypotheses[node.id].statement)
                      : labelForNode(node)}
                  </text>
                  {node.source && (
                    <text x={16} y={57} className="graph-node__src">
                      📡 {node.source}
                      {relTime(node.first_seen) ? ` · ${relTime(node.first_seen)}` : ""}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>

        {hoverEdge &&
          (() => {
            const edge = live.edges[hoverEdge.id];
            if (!edge) return null;
            const src = live.nodes[edge.src];
            const dst = live.nodes[edge.dst];
            const est = relTime(edge.established);
            const pct = edge.confidence != null ? Math.round(edge.confidence * 100) : null;
            const causal = Boolean(EDGE_COLORS[edge.type]) || edge.origin === "inferred";
            return (
              <div className="edge-tip" style={{ left: hoverEdge.x + 14, top: hoverEdge.y + 14 }}>
                <div className="edge-tip__rel">
                  {humanizeRelation(edge.type)}
                  <span className={`edge-tip__kind edge-tip__kind--${causal ? "causal" : "structural"}`}>
                    {causal ? "inferred" : "structural"}
                  </span>
                  {edge.provisional && <span className="prov-chip">provisional</span>}
                </div>
                <div className="edge-tip__dir">
                  <b>{src ? labelForNode(src) : shortId(edge.src)}</b>
                  <span className="edge-tip__arrow"> → </span>
                  <b>{dst ? labelForNode(dst) : shortId(edge.dst)}</b>
                </div>
                <dl className="edge-tip__meta">
                  <div>
                    <dt>origin</dt>
                    <dd>{edge.origin}</dd>
                  </div>
                  {edge.source && (
                    <div>
                      <dt>source</dt>
                      <dd>{edge.source}</dd>
                    </div>
                  )}
                  {est && (
                    <div>
                      <dt>established</dt>
                      <dd>{est}</dd>
                    </div>
                  )}
                  {pct != null && (
                    <div>
                      <dt>confidence</dt>
                      <dd>{pct}%</dd>
                    </div>
                  )}
                </dl>
              </div>
            );
          })()}

        {related.length > 0 && (
          <div className="related-panel">
            <div className="related-panel__title">
              <span className="related-panel__dot" /> Related incidents ({related.length})
            </div>
            <ul className="related-panel__list">
              {related.map((r) => (
                <li key={r.node.id}>
                  <button
                    className="related-chip"
                    onClick={() => onSelect({ kind: "node", id: r.node.id })}
                    title="Focus in graph"
                  >
                    <span className="related-chip__id">{labelForNode(r.node)}</span>
                    <span className="related-chip__rel">{r.relation.replace("_", " ")}</span>
                    {r.confidence != null && (
                      <span className="related-chip__conf">{Math.round(r.confidence * 100)}%</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {selected && (
          <div className="node-detail">
            <div className="node-detail__head">
              <div>
                <span className="node-detail__seq">#{orderFor.get(selected.id) ?? "?"}</span>
                <span className="node-detail__type">{selected.type}</span>
                <span className="node-detail__layer">{layerLabelForType(selected.type)}</span>
              </div>
              <button className="node-detail__close" onClick={() => onSelect(null)} aria-label="Close">
                ✕
              </button>
            </div>
            <code className="node-detail__id">{selected.id}</code>

            {selected.type === "hypothesis" && live.hypotheses[selected.id]?.statement && (
              <p className="node-detail__hyp">{live.hypotheses[selected.id].statement}</p>
            )}

            {(selected.source || selected.first_seen) && (
              <p className="node-detail__prov">
                {selected.source && (
                  <>
                    fetched from <strong>{selected.source}</strong>
                  </>
                )}
                {relTime(selected.first_seen) && <> · first seen {relTime(selected.first_seen)}</>}
              </p>
            )}

            {/* The node's datums, grouped by the SIX datum-shape categories (2026-07-23 primitives
                §2): identity · property · state (with change-trail) · reading · event · span. The
                engine's served species classifies each fact; identity_keys splits the props; work
                notes lift into their own journal. A section renders only when it has content. */}

            {/* IDENTITY — the write-once keys that MAKE this entity THIS entity (§2.1) */}
            {identityEntries.length > 0 && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--identity">Identity</h4>
                <dl className="node-detail__props">
                  {identityEntries.map(([k, v]) => (
                    <div key={k}>
                      <dt>{humanizePredicate(k)}</dt>
                      <dd>{formatValue(v)}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}

            {/* PROPERTY — timeless facts ABOUT it + renderable content (diff/blame) (§2.2) */}
            {(propertyEntries.length > 0 || contentFacts.length > 0) && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--property">Property</h4>
                <dl className="node-detail__props">
                  {propertyEntries.map(([k, v]) => (
                    <div key={k}>
                      <dt>{humanizePredicate(k)}</dt>
                      <dd>{formatValue(v)}</dd>
                    </div>
                  ))}
                  {contentFacts.map((f) => (
                    <div key={f.id} className={f.provisional ? "is-provisional" : ""}>
                      <dt>{humanizePredicate(f.predicate)}</dt>
                      <dd>
                        {formatValue(f.value)}
                        {f.unit ? ` ${f.unit}` : ""}
                        {f.provisional && <span className="prov-chip">provisional</span>}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}

            {/* STATE — a value TRUE over a window, shown as its supersede CHANGE-TRAIL (§2.3) */}
            {trails.length > 0 && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--state">
                  State <span className="cat__hint">change-trail</span>
                </h4>
                <ul className="node-detail__trails">
                  {trails.map((t) => {
                    const current = t.steps[t.steps.length - 1];
                    return (
                      <li
                        key={t.predicate}
                        className={t.steps.some((s) => s.id === selectedFactId) ? "is-highlight" : ""}
                      >
                        <div className="trail__now">
                          <strong>{humanizePredicate(t.predicate)}</strong> = {formatValue(current.value)}
                          {current.unit ? ` ${current.unit}` : ""}
                          {current.valid_to === null && <span className="trail__badge">now</span>}
                        </div>
                        {t.steps.length > 1 && (
                          <ol className="trail__steps">
                            {t.steps.map((s) => (
                              <li key={s.id} className={s.state === "superseded" ? "is-superseded" : ""}>
                                <span className="trail__val">
                                  {formatValue(s.value)}
                                  {s.unit ? ` ${s.unit}` : ""}
                                </span>
                                <span className="trail__win">
                                  {clockTime(s.at)}
                                  {s.valid_to ? ` → ${clockTime(s.valid_to)}` : " → now"}
                                </span>
                              </li>
                            ))}
                          </ol>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {/* READING — measured numbers qualified by a stat+window; append-only (§2.4) */}
            {readingFacts.length > 0 && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--reading">
                  Readings ({readingFacts.length})
                </h4>
                <ul className="node-detail__facts">
                  {readingFacts.map((f) => (
                    <li
                      key={f.id}
                      className={[f.id === selectedFactId ? "is-highlight" : "", f.provisional ? "is-provisional" : ""]
                        .join(" ")
                        .trim()}
                    >
                      <strong>{humanizePredicate(f.predicate)}</strong> = {formatValue(f.value)}
                      {f.unit ? ` ${f.unit}` : ""}
                      {clockTime(f.at) && <span className="node-detail__meta"> · {clockTime(f.at)}</span>}
                      {f.source && <span className="node-detail__meta"> · {f.source}</span>}
                      {f.provisional && <span className="prov-chip">provisional</span>}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* EVENT — discrete instant things that HAPPENED, immutable (§2.5) */}
            {selectedEvents.length > 0 && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--event">Events ({selectedEvents.length})</h4>
                <ul className="node-detail__events">
                  {selectedEvents.map((ev) => (
                    <li key={ev.id} className={ev.provisional ? "is-provisional" : ""}>
                      <span className="node-detail__evtype">{ev.type}</span>
                      {/* M4 — the source's own event-type spelling (provenance), when it differs */}
                      {ev.source_native_name && ev.source_native_name !== ev.type && (
                        <span className="node-detail__native" title="the source's own field name (provenance)">
                          {ev.source_native_name}
                        </span>
                      )}
                      {ev.at && <span className="node-detail__meta"> · {new Date(ev.at).toLocaleTimeString()}</span>}
                      {ev.provisional && <span className="prov-chip">provisional</span>}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* SPAN — bounded happenings with start→end · duration · phase (§2.6, the 6th species) */}
            {selectedSpans.length > 0 && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--span">Spans ({selectedSpans.length})</h4>
                <ul className="node-detail__spans">
                  {selectedSpans.map((s) => {
                    const dur = spanDuration(s);
                    const phase = s.span_phase ?? "—";
                    return (
                      <li key={s.id} className={s.provisional ? "is-provisional" : ""}>
                        <div className="span__head">
                          <strong>{humanizePredicate(s.name)}</strong>
                          <span className={`span__phase span__phase--${phase}`}>{phase}</span>
                        </div>
                        <div className="span__win">
                          {clockTime(s.started_at)} → {s.ended_at ? clockTime(s.ended_at) : "in-flight"}
                          {dur && <span className="span__dur"> · {dur}</span>}
                        </div>
                        {s.correlation_id && (
                          <div className="span__corr">
                            <span className="span__k">trace</span> {s.correlation_id}
                          </div>
                        )}
                        {s.provisional && <span className="prov-chip">provisional</span>}
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {/* WORK NOTES — the human audit JOURNAL (§3 ANNOTATION / the LOG→promoted trail): each
                independently-timestamped note is one entry, ordered. */}
            {workNotes.length > 0 && (
              <section className="cat">
                <h4 className="node-detail__sub cat__head cat__head--notes">Work notes ({workNotes.length})</h4>
                <ol className="node-detail__journal">
                  {workNotes.map((n, i) => (
                    <li key={i}>
                      {n.stamp && <span className="journal__stamp">{n.stamp}</span>}
                      {n.author && <span className="journal__author">{n.author}</span>}
                      <span className="journal__text">{n.text}</span>
                    </li>
                  ))}
                </ol>
              </section>
            )}

            {identityEntries.length === 0 &&
              propertyEntries.length === 0 &&
              contentFacts.length === 0 &&
              trails.length === 0 &&
              readingFacts.length === 0 &&
              selectedEvents.length === 0 &&
              selectedSpans.length === 0 &&
              workNotes.length === 0 && (
                <p className="node-detail__empty">No datums recorded on this node yet.</p>
              )}
          </div>
        )}
      </div>
    </div>
  );
}
