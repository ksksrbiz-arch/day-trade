// Hand-editable SEED topology (fallback only).
// The LIVE topology is fetched from the platform at /api/telemetry/topology and
// reflects the real components. Edit this seed if you want an offline default or
// to tweak grouping; live data overrides it when the platform is reachable.
import type { GraphNode, GraphEdge } from "./contract";

export const SEED_TOPOLOGY: { nodes: GraphNode[]; edges: GraphEdge[] } = {
  nodes: [
    { id: "m:anthropic", label: "anthropic", kind: "model", group: "Reasoning Council" },
    { id: "m:groq", label: "groq", kind: "model", group: "Reasoning Council" },
    { id: "m:cloudflare", label: "cloudflare", kind: "model", group: "Reasoning Council" },
    { id: "l:reasoning", label: "Reasoning", kind: "layer", group: "Cognition" },
    { id: "l:brain", label: "Brain", kind: "layer", group: "Cognition" },
    { id: "l:prediction", label: "Prediction", kind: "layer", group: "Cognition" },
    { id: "l:ml", label: "ML", kind: "layer", group: "Cognition" },
    { id: "l:mesh", label: "Mesh", kind: "layer", group: "Cognition" },
    { id: "a:quant_researcher", label: "Quant Researcher", kind: "agent", group: "Desk" },
    { id: "a:risk_officer", label: "Risk Officer", kind: "agent", group: "Desk" },
    { id: "t:run_backtest", label: "run_backtest", kind: "tool", group: "Tools" },
    { id: "t:ml_card", label: "ml_card", kind: "tool", group: "Tools" },
    { id: "s:agents", label: "agents", kind: "service", group: "Runtime" },
    { id: "d:mesh", label: "mesh.db", kind: "datastore", group: "Memory" },
    { id: "d:ltm", label: "Pieces LTM", kind: "datastore", group: "Memory" },
  ],
  edges: [
    { id: "e1", source: "m:anthropic", target: "l:reasoning" },
    { id: "e2", source: "m:groq", target: "l:reasoning" },
    { id: "e3", source: "m:cloudflare", target: "l:reasoning" },
    { id: "e4", source: "a:quant_researcher", target: "t:run_backtest" },
    { id: "e5", source: "a:risk_officer", target: "l:mesh" },
    { id: "e6", source: "l:reasoning", target: "l:mesh" },
    { id: "e7", source: "l:mesh", target: "d:mesh" },
    { id: "e8", source: "l:mesh", target: "d:ltm" },
    { id: "e9", source: "s:agents", target: "l:reasoning" },
    { id: "e10", source: "l:prediction", target: "l:brain" },
  ],
};
