# Platform Brain — live 3D neural visualization

Renders the trading platform as a living graph you can watch think: nodes are
components (models, agents, layers, tools, services, datastores), edges are
communication paths (dendrites), and live actions render as **synapses firing** —
pulses of light traveling source→target, brightening the edge and pulsing the
target node on arrival, color-coded by event type, glowing via bloom.

Standalone Next.js (App Router, TypeScript) app at route **`/brain`**. Wired
straight to the live platform telemetry — **no mock**.

## Run

```bash
# 1) platform must be running (serves topology + SSE on :8000)
#    (the paper-trader dashboard: python -m uvicorn dashboard.app:app --port 8000)
# 2) brain app
cd brain
npm install
npm run dev          # http://localhost:3000/brain   (use localhost, not 127.0.0.1)
```

Open **http://localhost:3000/brain**. You'll see the graph; the status pill shows
"live telemetry · N events" and synapses fire as the platform acts.

> Use `localhost` (Next 16 dev blocks cross-origin dev resources; `127.0.0.1` is
> treated as a different origin — `allowedDevOrigins` in `next.config.mjs` covers
> both, but `localhost` is cleanest).

## Architecture

```
 platform (Python, :8000)                      brain app (Next, :3000)
 ─────────────────────────                     ────────────────────────
 dashboard/telemetry.py                         src/eventSource/live.ts  (EventSource impl)
   build_topology() ───────GET /api/telemetry/topology──▶ getTopology()
   fire_events_since() ──── SSE /api/telemetry/stream ──▶ subscribe(onFire)
                                                           │
                                                  src/store.ts (Zustand)
                                                   ├─ reactive slice (HUD)
                                                   └─ engine: pulses[], activation{} (60fps, no re-render)
                                                           │
                                            src/scene/BrainScene.tsx (react-three-fiber)
                                              Nodes (instanced) · Edges (heat) · Pulses (pooled) · Bloom
                                              src/layout3d.ts (d3-force-3d, computed once)
                                            src/hud/Hud.tsx (legend, filters, node panel, controls)
```

## The contract (`src/contract.ts`)

Everything downstream reads ONLY this — nothing touches a data source directly.

```ts
type NodeKind = 'model'|'agent'|'layer'|'tool'|'service'|'datastore';
type GraphNode = { id; label; kind: NodeKind; group?; meta? };
type GraphEdge = { id; source; target; kind? };
type FireKind  = 'model_call'|'tool_call'|'response'|'data_read'|'data_write'|'error';
type FireEvent = { id; source; target; kind: FireKind; ts; durationMs?; status?; summary? };
interface EventSource {
  getTopology(): Promise<{ nodes; edges }>;
  subscribe(onFire: (e: FireEvent) => void): () => void;   // returns unsubscribe
}
```

`LiveEventSource` is the single implementation (the real adapter). To point the
brain at a different backend, implement this one interface.

## Live payload shape (what the platform emits)

- **Topology** — `GET /api/telemetry/topology` → `{ nodes: GraphNode[], edges: GraphEdge[] }`.
- **Stream** — `GET /api/telemetry/stream` is Server-Sent Events; each `data:`
  line is exactly one `FireEvent` JSON:

  ```
  data: {"id":"tr42","source":"a:quant_researcher","target":"t:run_backtest",
         "kind":"tool_call","ts":1782450000000,"status":"ok","durationMs":1700,
         "summary":"backtest edge -20% vs SPY"}
  ```

  `source`/`target` must be node ids present in the topology; `kind` is one of the
  six `FireKind`s. To emit events from any other system, stream JSON of this shape.

### Where the live events come from (Python `dashboard/telemetry.py`)
- **agent execution traces** → `agent → tool/layer` (`tool_call` / `model_call` / `error`)
- **insight mesh** writes → `layer → mesh datastore` (`data_write`)
- **council votes** → `model → reasoning layer` (`model_call`)

Add more sources by extending `fire_events_since()` — map any platform event to a
`(source_id, target_id, kind)` triple using the node-id helpers.

## Editing the topology

The **live** topology is generated from the platform's real registries
(`build_topology()` in `dashboard/telemetry.py`) — edit there to change what nodes
appear. A hand-editable offline **seed** also lives in `src/topology.ts` (used as a
fallback / reference). Node ids follow `m:` model, `a:` agent, `l:` layer, `t:`
tool, `s:` service, `d:` datastore.

## Visual language

- **Nodes** sized + colored by kind (models = large warm spheres, datastores =
  cubes, tools = small). Subtle idle "breathing"; brighten + scale on activation.
- **Edges** dim at rest; heat up briefly when a synapse crosses them.
- **Pulses** travel source→target, color-coded by `FireKind`; bloom makes active
  ones glow. Object-pooled, capped (`engine.MAX_PULSES`), decayed.
- **HUD**: top-left controls (pause/play, auto-rotate, event-rate sampling),
  bottom-left legend + click-to-filter by `FireKind`, click a node for a side
  panel with its live recent activity, bottom-right global synapse feed.

## Performance

- Nodes and pulses use **instanced** meshes; pulses are **object-pooled** and
  capped; activations/edge-heat decay each frame. The 60fps animation reads the
  non-reactive `engine` state via `getState()` in `useFrame` (no React re-render
  per event); only the capped HUD slices use reactive `set`.

## Notes
- Only intentional integration point: the telemetry endpoints. Swap backend by
  reimplementing `EventSource`.
- `error`-kind synapses (red) are real — they reflect actual failed steps in the
  platform, so a flurry of red is the brain honestly showing trouble.
