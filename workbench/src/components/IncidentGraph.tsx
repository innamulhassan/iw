import { useMemo, useState } from "react";
import type { Graph, GraphEdge, GraphNode, HypothesisItem } from "../types";
import { TIER_LABELS, TIER_ORDER, tierForType } from "../lib/tiers";

interface Props {
  graph: Graph;
  hypotheses: HypothesisItem[];
}

const NODE_W = 168;
const NODE_H = 60;
const COL_GAP = 232;
const ROW_GAP = 92;
const MARGIN_X = 60;
const MARGIN_Y = 46;

const PREFERRED_LABEL_KEYS = [
  "service_name",
  "alert_id",
  "change_id",
  "incident_id",
  "db_id",
  "sha",
  "signature_hash",
  "anomaly_id",
  "statement",
];

function shortId(id: string): string {
  const idx = id.indexOf(":");
  return idx >= 0 ? id.slice(idx + 1) : id;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function labelForNode(node: GraphNode): string {
  const props = node.props ?? {};
  for (const key of PREFERRED_LABEL_KEYS) {
    const value = props[key];
    if (value === undefined || value === null) continue;
    let text = String(value);
    if (key === "sha") text = text.slice(0, 7);
    return text;
  }
  return shortId(node.id);
}

/** Greedily wraps `text` into at most two lines of `maxChars` characters,
 * ellipsizing whatever doesn't fit — used so long hypothesis statements
 * stay inside their node card instead of bleeding into the next column. */
function wrapTwoLines(text: string, maxChars: number): string[] {
  if (text.length <= maxChars) return [text];

  const words = text.split(/\s+/);
  let line1 = "";
  let i = 0;
  while (i < words.length) {
    const candidate = line1 ? `${line1} ${words[i]}` : words[i];
    if (candidate.length > maxChars) break;
    line1 = candidate;
    i++;
  }
  if (!line1) {
    line1 = words[0].slice(0, maxChars);
    i = 1;
  }

  let remainder = words.slice(i).join(" ");
  if (!remainder) return [line1];
  if (remainder.length > maxChars) {
    remainder = `${remainder.slice(0, maxChars - 1)}…`;
  }
  return [line1, remainder];
}

const EDGE_COLORS: Record<string, string> = {
  caused_by: "#7c3aed",
  supports: "#15803d",
  refutes: "#dc2626",
  correlated_with: "#d97706",
};

const STRUCTURAL_COLOR = "#94a3b8";

function colorForEdge(edge: GraphEdge): string {
  if (EDGE_COLORS[edge.type]) return EDGE_COLORS[edge.type];
  if (edge.origin === "inferred") return "#7c3aed";
  return STRUCTURAL_COLOR;
}

function isCausal(edge: GraphEdge): boolean {
  return Boolean(EDGE_COLORS[edge.type]) || edge.origin === "inferred";
}

function markerId(color: string): string {
  return `arrow-${color.replace("#", "")}`;
}

export default function IncidentGraph({ graph, hypotheses }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { positions, columns, width, height } = useMemo(() => {
    const cols = TIER_ORDER.map((tier) => ({
      tier,
      nodes: graph.nodes.filter((n) => tierForType(n.type) === tier),
    })).filter((col) => col.nodes.length > 0);

    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((col, ci) => {
      const x = MARGIN_X + ci * COL_GAP + NODE_W / 2;
      col.nodes.forEach((node, ri) => {
        const y = MARGIN_Y + ri * ROW_GAP + NODE_H / 2;
        pos.set(node.id, { x, y });
      });
    });

    const maxRows = Math.max(1, ...cols.map((c) => c.nodes.length));
    const w = MARGIN_X * 2 + Math.max(cols.length, 1) * COL_GAP;
    const h = MARGIN_Y * 2 + maxRows * ROW_GAP + 16;

    return { positions: pos, columns: cols, width: w, height: h };
  }, [graph.nodes]);

  const confirmedRootCandidates = useMemo(() => {
    return new Set(
      hypotheses
        .filter((item) => item.status === "confirmed" && item.root_candidate)
        .map((item) => item.root_candidate as string)
    );
  }, [hypotheses]);

  const usedColors = useMemo(() => {
    const set = new Set<string>();
    graph.edges.forEach((e) => set.add(colorForEdge(e)));
    return Array.from(set);
  }, [graph.edges]);

  const selectedNode = selectedId ? graph.nodes.find((n) => n.id === selectedId) ?? null : null;
  const activeFacts = selectedId
    ? graph.facts.filter((f) => f.subject === selectedId && f.state === "active")
    : [];

  return (
    <div className="graph-pane">
      <div className="graph-pane__header">
        <h2 className="pane-title">Incident Graph</h2>
        <div className="graph-legend">
          <span className="graph-legend__item">
            <svg width="26" height="10" aria-hidden="true">
              <line x1="0" y1="5" x2="26" y2="5" stroke={STRUCTURAL_COLOR} strokeWidth={2} />
            </svg>
            structural
          </span>
          <span className="graph-legend__item">
            <svg width="26" height="10" aria-hidden="true">
              <line x1="0" y1="5" x2="26" y2="5" stroke="#7c3aed" strokeWidth={2} strokeDasharray="5 4" />
            </svg>
            causal / inferred
          </span>
          <span className="graph-legend__item">
            <span className="legend-swatch legend-swatch--symptom" /> symptom
          </span>
          <span className="graph-legend__item">
            <span className="legend-swatch legend-swatch--root" /> confirmed root cause
          </span>
        </div>
      </div>

      <div className="graph-canvas-scroll">
        <svg
          className="graph-canvas"
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label="Incident causal graph"
        >
          <defs>
            {usedColors.map((color) => (
              <marker
                key={color}
                id={markerId(color)}
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

          {columns.map((col, ci) => (
            <text
              key={col.tier}
              x={MARGIN_X + ci * COL_GAP + NODE_W / 2}
              y={20}
              className="graph-column-label"
              textAnchor="middle"
            >
              {TIER_LABELS[col.tier]}
            </text>
          ))}

          {graph.edges.map((edge) => {
            const from = positions.get(edge.src);
            const to = positions.get(edge.dst);
            if (!from || !to) return null;

            const dx = to.x - from.x;
            const dy = to.y - from.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const nx = -dy / dist;
            const ny = dx / dist;
            const sameColumn = from.x === to.x;
            const bow = sameColumn ? 46 : 18;
            const sign = edge.id.length % 2 === 0 ? 1 : -1;
            const midX = (from.x + to.x) / 2 + nx * bow * sign;
            const midY = (from.y + to.y) / 2 + ny * bow * sign;
            const color = colorForEdge(edge);
            const causal = isCausal(edge);

            return (
              <path
                key={edge.id}
                d={`M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`}
                fill="none"
                stroke={color}
                strokeWidth={causal ? 2 : 1.75}
                strokeDasharray={causal ? "6 4" : undefined}
                markerEnd={`url(#${markerId(color)})`}
                className={causal ? "edge edge--causal" : "edge edge--structural"}
              >
                <title>{`${edge.type} (${edge.origin})`}</title>
              </path>
            );
          })}

          {graph.nodes.map((node) => {
            const pos = positions.get(node.id);
            if (!pos) return null;
            const isSymptom = node.type === "anomaly";
            const isRoot = confirmedRootCandidates.has(node.id);
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
                  isRoot ? "graph-node--root" : "",
                  isSelected ? "graph-node--selected" : "",
                ]
                  .join(" ")
                  .trim()}
                onClick={() => setSelectedId(node.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedId(node.id);
                  }
                }}
                tabIndex={0}
                role="button"
                aria-pressed={isSelected}
              >
                <rect width={NODE_W} height={NODE_H} rx={10} className="graph-node__rect" />
                <text x={10} y={20} className="graph-node__type">
                  {node.type}
                </text>
                {wrapTwoLines(labelForNode(node), 21).map((line, i, arr) => (
                  <text
                    key={i}
                    x={10}
                    y={arr.length === 1 ? 41 : 34 + i * 15}
                    className="graph-node__label"
                  >
                    {line}
                  </text>
                ))}
                {isSymptom && (
                  <circle cx={NODE_W - 12} cy={12} r={5} className="graph-node__badge graph-node__badge--symptom">
                    <title>Symptom (anomaly)</title>
                  </circle>
                )}
                {isRoot && (
                  <circle
                    cx={isSymptom ? NODE_W - 28 : NODE_W - 12}
                    cy={12}
                    r={5}
                    className="graph-node__badge graph-node__badge--root"
                  >
                    <title>Confirmed root cause</title>
                  </circle>
                )}
              </g>
            );
          })}
        </svg>
      </div>

      <div className="graph-inspector">
        {selectedNode ? (
          <>
            <div className="graph-inspector__header">
              <span className="graph-inspector__type">{selectedNode.type}</span>
              <code className="graph-inspector__id">{selectedNode.id}</code>
            </div>
            <div className="graph-inspector__props">
              {Object.entries(selectedNode.props ?? {}).map(([key, value]) => (
                <div key={key} className="graph-inspector__prop-row">
                  <span className="graph-inspector__prop-key">{key}</span>
                  <span className="graph-inspector__prop-value">{formatValue(value)}</span>
                </div>
              ))}
            </div>
            <div className="graph-inspector__facts">
              <h4>Active facts</h4>
              {activeFacts.length === 0 ? (
                <p className="graph-inspector__empty">No active facts for this node.</p>
              ) : (
                <ul>
                  {activeFacts.map((fact) => (
                    <li key={fact.id}>
                      <strong>{fact.predicate}</strong> = {formatValue(fact.value)}
                      {fact.unit ? ` ${fact.unit}` : ""}
                      <span className="graph-inspector__fact-meta">
                        {" "}
                        · {fact.source} · {new Date(fact.at).toLocaleTimeString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        ) : (
          <p className="graph-inspector__empty">
            Click a node to inspect its properties and active facts.
          </p>
        )}
      </div>
    </div>
  );
}
