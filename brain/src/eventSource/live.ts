// LiveEventSource — the REAL adapter. Wires straight to the platform telemetry.
//
// Topology:  GET  {BASE}/api/telemetry/topology  -> { nodes, edges }
// Stream:    SSE  {BASE}/api/telemetry/stream     -> `data: <FireEvent JSON>\n\n`
//
// Expected live payload (each SSE `data:` line) is exactly a FireEvent:
//   { id, source, target, kind, ts, durationMs?, status?, summary? }
// where source/target are node ids present in the topology, and kind is one of
// model_call | tool_call | response | data_read | data_write | error.
//
// To emit matching events from any other system, POST/stream JSON of that shape.
import type { EventSource as IEventSource, FireEvent, GraphNode, GraphEdge } from "../contract";
import { FIRE_KINDS } from "../contract";

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "http://127.0.0.1:8000";

export class LiveEventSource implements IEventSource {
  private base: string;
  constructor(base: string = BASE) {
    this.base = base.replace(/\/$/, "");
  }

  async getTopology(): Promise<{ nodes: GraphNode[]; edges: GraphEdge[] }> {
    // retry: the platform may still be warming when the page first loads
    let lastErr: unknown;
    for (let i = 0; i < 6; i++) {
      try {
        const r = await fetch(`${this.base}/api/telemetry/topology`, { cache: "no-store" });
        if (r.ok) return r.json();
        lastErr = new Error(`topology ${r.status}`);
      } catch (e) { lastErr = e; }
      await new Promise((res) => setTimeout(res, 1500));
    }
    throw lastErr;
  }

  subscribe(onFire: (e: FireEvent) => void): () => void {
    const es = new window.EventSource(`${this.base}/api/telemetry/stream`);
    es.onmessage = (msg) => {
      if (!msg.data || msg.data.startsWith(":")) return;
      try {
        const e = JSON.parse(msg.data) as FireEvent;
        if (e && typeof e.id === "string" && FIRE_KINDS.includes(e.kind)) onFire(e);
      } catch {
        /* ignore malformed frame */
      }
    };
    es.onerror = () => {
      // browser EventSource auto-reconnects; nothing to do
    };
    return () => es.close();
  }
}
