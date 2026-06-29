import { create } from "zustand";
import type { FireEvent, FireKind, GraphEdge, GraphNode } from "./contract";
import { FIRE_KINDS } from "./contract";

export type Pulse = {
  from: string;
  to: string;
  kind: FireKind;
  start: number; // perf ms
  dur: number;
  status?: string;
};

// ---- non-reactive engine state (mutated in useFrame; never triggers React) ---
export const engine = {
  pulses: [] as Pulse[],
  activation: {} as Record<string, number>, // node id -> 0..1 glow, decays
  edgeHeat: {} as Record<string, number>, // "src->tgt" -> 0..1, decays
  MAX_PULSES: 220,
};

type Filters = Record<FireKind, boolean>;

interface State {
  nodes: GraphNode[];
  edges: GraphEdge[];
  byId: Record<string, GraphNode>;
  positions: Record<string, [number, number, number]>;
  connected: boolean;
  paused: boolean;
  rate: number; // 0..1 sampling of incoming events
  autoRotate: boolean;
  filters: Filters;
  groupHidden: Record<string, boolean>;
  selected: string | null;
  hovered: string | null;
  feed: FireEvent[]; // global recent (capped)
  perNode: Record<string, FireEvent[]>;
  counts: Record<FireKind, number>;
  total: number;
  setTopology: (n: GraphNode[], e: GraphEdge[], pos: Record<string, [number, number, number]>) => void;
  setConnected: (b: boolean) => void;
  togglePause: () => void;
  setRate: (r: number) => void;
  toggleRotate: () => void;
  toggleFilter: (k: FireKind) => void;
  toggleGroup: (g: string) => void;
  select: (id: string | null) => void;
  setHover: (id: string | null) => void;
  addFire: (e: FireEvent) => void;
}

const allOn = (): Filters =>
  FIRE_KINDS.reduce((a, k) => ((a[k] = true), a), {} as Filters);
const zeroCounts = (): Record<FireKind, number> =>
  FIRE_KINDS.reduce((a, k) => ((a[k] = 0), a), {} as Record<FireKind, number>);

export const useStore = create<State>((set, get) => ({
  nodes: [],
  edges: [],
  byId: {},
  positions: {},
  connected: false,
  paused: false,
  rate: 1,
  autoRotate: true,
  filters: allOn(),
  groupHidden: {},
  selected: null,
  hovered: null,
  feed: [],
  perNode: {},
  counts: zeroCounts(),
  total: 0,

  setTopology: (nodes, edges, positions) =>
    set({ nodes, edges, positions, byId: Object.fromEntries(nodes.map((n) => [n.id, n])) }),
  setConnected: (connected) => set({ connected }),
  togglePause: () => set((s) => ({ paused: !s.paused })),
  setRate: (rate) => set({ rate }),
  toggleRotate: () => set((s) => ({ autoRotate: !s.autoRotate })),
  toggleFilter: (k) => set((s) => ({ filters: { ...s.filters, [k]: !s.filters[k] } })),
  toggleGroup: (g) => set((s) => ({ groupHidden: { ...s.groupHidden, [g]: !s.groupHidden[g] } })),
  select: (selected) => set({ selected }),
  setHover: (hovered) => set({ hovered }),

  addFire: (e) => {
    const s = get();
    if (s.paused) return;
    if (!s.filters[e.kind]) return;
    if (s.rate < 1 && Math.random() > s.rate) return;
    if (!s.byId[e.source] || !s.byId[e.target]) return;

    // engine (mutable, no re-render)
    if (engine.pulses.length < engine.MAX_PULSES) {
      engine.pulses.push({
        from: e.source, to: e.target, kind: e.kind,
        start: performance.now(), dur: 650, status: e.status,
      });
    }
    engine.activation[e.target] = 1;
    engine.activation[e.source] = Math.max(engine.activation[e.source] || 0, 0.55);
    engine.edgeHeat[`${e.source}->${e.target}`] = 1;

    // reactive (HUD) — capped, cheap
    const feed = [e, ...s.feed].slice(0, 80);
    const pn = s.perNode;
    const a = [e, ...(pn[e.source] || [])].slice(0, 25);
    const b = [e, ...(pn[e.target] || [])].slice(0, 25);
    set({
      feed,
      perNode: { ...pn, [e.source]: a, [e.target]: b },
      counts: { ...s.counts, [e.kind]: s.counts[e.kind] + 1 },
      total: s.total + 1,
    });
  },
}));

if (typeof window !== "undefined") (window as any).__brain = useStore;
