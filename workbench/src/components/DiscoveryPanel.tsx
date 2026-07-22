import type { DiscoveryCounters } from "../types";

function sortedCounts(rec: Record<string, number>): [string, number][] {
  return Object.entries(rec).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

// The PROMOTION SIGNAL (P3 airlock step 5): the engine counts how often an unknown class_hint
// or a quarantined `x.<source>.<native>` name recurs — "the system keeps seeing unknown X".
// A repeated hint means a NodeType is missing; a repeated name means a DictEntry/alias is
// missing. Surfaced for the HUMAN to make the core-registry edit — never applied automatically.
export default function DiscoveryPanel({ discovery }: { discovery: DiscoveryCounters }) {
  const hints = sortedCounts(discovery.class_hints);
  const names = sortedCounts(discovery.quarantined_names);
  if (hints.length === 0 && names.length === 0) return null;

  return (
    <div className="discovery">
      <h2 className="pane-title">Discovery</h2>
      <p className="pane-subtitle">
        Unknowns the engine keeps seeing — recurring signals that a registry entry is missing.
        Promotion is a human edit; the engine only counts.
      </p>
      {hints.length > 0 && (
        <div className="discovery__group">
          <span className="discovery__label">Unknown node classes</span>
          <ul className="discovery__list">
            {hints.map(([name, n]) => (
              <li key={name} className="discovery-chip" title="class_hint recurring on generic_ci nodes — a NodeType may be missing">
                <code className="discovery-chip__name">{name}</code>
                <span className="discovery-chip__count">×{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {names.length > 0 && (
        <div className="discovery__group">
          <span className="discovery__label">Quarantined names</span>
          <ul className="discovery__list">
            {names.map(([name, n]) => (
              <li key={name} className="discovery-chip" title="airlocked open-vocabulary name recurring on provisional facts/events — a dictionary entry may be missing">
                <code className="discovery-chip__name">{name}</code>
                <span className="discovery-chip__count">×{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
