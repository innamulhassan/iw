// Architectural LAYER lanes (obs 4): the graph lays nodes out by the architectural layer they
// belong to — Case → Signal → Service → Messaging → Database → Infra → Network → Change — which
// mirrors how APM/topology tools (Dynatrace Smartscape, AppDynamics flow maps) group entities,
// and encodes the ServiceNow chain origin(incident) → symptom → topology → candidate change.
// The lane is a PURE FUNCTION of node `type`, derived from the engine's L0-L6 NodeType groups —
// never hand-assigned per node. (Export names kept as `tier*` so the layout code is untouched.)

export type Tier =
  | "case"
  | "signal"
  | "service"
  | "messaging"
  | "database"
  | "infra"
  | "network"
  | "change";

const TIER_BY_TYPE: Record<string, Tier> = {
  // Case — origin + reasoning
  incident: "case",
  hypothesis: "case",
  // Signal — symptom / observation
  anomaly: "signal",
  alert: "signal",
  error_signature: "signal",
  business_transaction: "signal",
  metric: "signal",
  // Service — app tier
  service: "service",
  application: "service",
  component: "service",
  api_endpoint: "service",
  process: "service",
  external_service: "service",
  team: "service",
  // Messaging — async transport
  message_queue: "messaging",
  queue: "messaging",
  // Database — datastore
  database: "database",
  schema: "database",
  cache: "database",
  // Infra — compute / platform
  host: "infra",
  cluster: "infra",
  namespace: "infra",
  pod: "infra",
  container: "infra",
  replicaset: "infra",
  deployment: "infra",
  batch_job: "infra",
  config_item: "infra",
  generic_ci: "infra",
  // Network — wire + edge
  load_balancer: "network",
  route: "network",
  dns: "network",
  dns_record: "network",
  proxy: "network",
  api_gateway: "network",
  cdn: "network",
  waf: "network",
  network_segment: "network",
  firewall_rule: "network",
  network_device: "network",
  certificate: "network",
  // Change — candidate causes (time + CI scoped)
  change_event: "change",
  code_commit: "change",
  pull_request: "change",
  release: "change",
  build_artifact: "change",
  feature_flag: "change",
};

export const TIER_ORDER: Tier[] = [
  "case",
  "signal",
  "service",
  "messaging",
  "database",
  "infra",
  "network",
  "change",
];

export const TIER_LABELS: Record<Tier, string> = {
  case: "Case",
  signal: "Signal",
  service: "Service",
  messaging: "Messaging",
  database: "Database",
  infra: "Infra",
  network: "Network",
  change: "Change",
};

/** Best-effort lane lookup for a node `type`, with keyword fallbacks for unseen types so the
 * graph never silently drops a node. */
export function tierForType(type: string): Tier {
  if (TIER_BY_TYPE[type]) return TIER_BY_TYPE[type];

  const t = type.toLowerCase();
  if (/queue|kafka|topic|broker|stream/.test(t)) return "messaging";
  if (/db|database|schema|cache|storage|table/.test(t)) return "database";
  if (/network|dns|lb|load_bal|router|firewall|proxy|gateway|cdn|waf|segment|cert/.test(t))
    return "network";
  if (/host|cluster|node|pod|container|namespace|replica|deploy|infra/.test(t)) return "infra";
  if (/commit|change|release|migration|flag|artifact|pull_request/.test(t)) return "change";
  if (/anomaly|alert|signal|signature|metric|transaction/.test(t)) return "signal";
  if (/incident|hypothesis/.test(t)) return "case";
  return "service";
}

/** The human-readable layer label for a node type (the on-node LAYER chip, obs 4). */
export function layerLabelForType(type: string): string {
  return TIER_LABELS[tierForType(type)];
}

const CAUSAL_EDGE_TYPES = new Set(["caused_by", "supports", "refutes", "correlated_with"]);

/** True for the inferred/causal analytical layer; false for the declared
 * or discovered structural spine (depends_on, runs_on, affects, ...). */
export function isCausalEdge(type: string, origin: string): boolean {
  return CAUSAL_EDGE_TYPES.has(type) || origin === "inferred";
}
