// The single typed contract the whole visualization consumes.
// Nothing downstream reaches into a data source directly — everything reads this.

export type NodeKind = "model" | "agent" | "layer" | "tool" | "service" | "datastore" | "connector";

export type GraphNode = {
  id: string;
  label: string;
  kind: NodeKind;
  group?: string;
  meta?: Record<string, unknown>;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  kind?: string;
};

export type FireKind =
  | "model_call"
  | "tool_call"
  | "response"
  | "data_read"
  | "data_write"
  | "error";

export type FireEvent = {
  id: string;
  source: string; // node id
  target: string; // node id
  kind: FireKind;
  ts: number; // epoch ms
  durationMs?: number;
  status?: "ok" | "error" | "pending";
  summary?: string; // short human label for the HUD
};

export interface EventSource {
  getTopology(): Promise<{ nodes: GraphNode[]; edges: GraphEdge[] }>;
  subscribe(onFire: (e: FireEvent) => void): () => void; // returns unsubscribe
}

// Color-coding per FireKind (hex used by both HUD legend and 3D pulses).
export const FIRE_COLORS: Record<FireKind, string> = {
  model_call: "#7c5cff", // violet
  tool_call: "#22d3ee", // cyan
  response: "#34d399", // green
  data_read: "#f5c451", // amber
  data_write: "#f97316", // orange
  error: "#ef4444", // red
};

export const KIND_COLORS: Record<NodeKind, string> = {
  model: "#ff8a5c", // warm
  agent: "#8b9bff",
  layer: "#5cc8ff",
  tool: "#3ad6c0",
  service: "#a0a8b8",
  datastore: "#c9a227",
  connector: "#ff5ca8", // external MCP / data feeds (Pieces, Alpaca, CoinEx, WSB, news)
};

export const FIRE_KINDS: FireKind[] = [
  "model_call",
  "tool_call",
  "response",
  "data_read",
  "data_write",
  "error",
];

export function isValidFire(e: unknown, ids: Set<string>): e is FireEvent {
  if (!e || typeof e !== "object") return false;
  const f = e as FireEvent;
  return (
    typeof f.id === "string" &&
    ids.has(f.source) &&
    ids.has(f.target) &&
    FIRE_KINDS.includes(f.kind) &&
    typeof f.ts === "number"
  );
}
