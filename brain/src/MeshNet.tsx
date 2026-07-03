"use client";
import { useEffect, useRef } from "react";

// Live AGENT-MESH network. Columns = node groups (Connectors -> Council -> Cognition
// -> Memory -> Runtime -> Tools -> Desk). Neuron brightness = decaying recent
// activity; particles are spawned by the REAL fire-event stream (each event =
// a glowing pulse traveling source->target, colored by kind). Nothing pre-baked.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "http://127.0.0.1:8000";

const COL_ORDER = ["Connectors", "Reasoning Council", "Cognition", "Memory",
                   "Runtime", "Tools", "Desk", "Macro"];
const SHORT: Record<string, string> = {
  "Reasoning Council": "COUNCIL", "Cognition": "COGNITION", "Connectors": "CONNECTORS",
  "Memory": "MEMORY", "Runtime": "RUNTIME", "Tools": "TOOLS", "Desk": "DESK", "Macro": "MACRO",
};
const KIND_COLOR: Record<string, number[]> = {
  model_call: [168, 85, 247], tool_call: [53, 224, 216], response: [52, 211, 153],
  data_read: [242, 165, 58], data_write: [251, 146, 60], error: [255, 90, 106],
};

type N = { id: string; label: string; col: number; idx: number; n: number; act: number };
type P = { s: string; t: string; born: number; life: number; col: number[] };

export default function MeshNet() {
  const cvs = useRef<HTMLCanvasElement>(null);
  const nodes = useRef<Map<string, N>>(new Map());
  const edges = useRef<{ s: string; t: string }[]>([]);
  const parts = useRef<P[]>([]);

  useEffect(() => {
    let alive = true;
    fetch(`${BASE}/api/telemetry/topology`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { nodes: any[]; edges: any[] }) => {
        if (!alive) return;
        const groups = COL_ORDER.filter((g) => d.nodes.some((n) => (n.group || "") === g));
        const counts: Record<number, number> = {};
        const m = new Map<string, N>();
        for (const n of d.nodes) {
          const col = groups.indexOf(n.group || "");
          if (col < 0) continue;
          counts[col] = (counts[col] || 0) + 1;
        }
        const CAP = 12;
        const byCol: Record<number, any[]> = {};
        for (const n of d.nodes) {
          const col = groups.indexOf(n.group || "");
          if (col < 0) continue;
          (byCol[col] = byCol[col] || []).push(n);
        }
        for (const col of Object.keys(byCol).map(Number)) {
          let list = byCol[col];
          if (list.length > CAP) {                       // sample evenly to balance columns
            const step = list.length / CAP;
            list = Array.from({ length: CAP }, (_, k) => list[Math.floor(k * step)]);
          }
          list.forEach((n, idx) =>
            m.set(n.id, { id: n.id, label: n.label || n.id, col, idx, n: list.length, act: 0.16 }));
        }
        (m as any)._groups = groups;
        nodes.current = m;
        edges.current = (d.edges || []).filter((e) => m.has(e.source) && m.has(e.target))
          .map((e) => ({ s: e.source, t: e.target }));
      })
      .catch(() => {});

    let es: EventSource | null = null;
    try {
      es = new EventSource(`${BASE}/api/telemetry/stream`);
      es.onmessage = (msg) => {
        if (!msg.data || msg.data.startsWith(":")) return;
        try {
          const e = JSON.parse(msg.data);
          const m = nodes.current;
          if (!e || !m.has(e.source) || !m.has(e.target)) return;
          m.get(e.source)!.act = 1; m.get(e.target)!.act = Math.max(m.get(e.target)!.act, 0.85);
          const col = KIND_COLOR[e.kind] || [150, 200, 255];
          if (parts.current.length < 600)
            parts.current.push({ s: e.source, t: e.target, born: performance.now(), life: 1300, col });
        } catch { /* ignore */ }
      };
    } catch { /* no EventSource */ }
    return () => { alive = false; es?.close(); };
  }, []);

  useEffect(() => {
    const c = cvs.current; if (!c) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let raf = 0;
    const frame = (ts: number) => {
      const W = window.innerWidth, H = window.innerHeight;
      if (c.width !== W * dpr) { c.width = W * dpr; c.height = H * dpr; c.style.width = W + "px"; c.style.height = H + "px"; }
      const ctx = c.getContext("2d"); if (!ctx) { raf = requestAnimationFrame(frame); return; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const bg = ctx.createRadialGradient(W * 0.5, H * 0.45, 80, W * 0.5, H * 0.5, Math.max(W, H) * 0.7);
      bg.addColorStop(0, "#0a1016"); bg.addColorStop(1, "#04070a");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

      const m = nodes.current;
      if (m.size === 0) { ctx.fillStyle = "#567"; ctx.font = "14px ui-monospace,monospace"; ctx.fillText("loading mesh topology…", 40, 70); raf = requestAnimationFrame(frame); return; }
      const groups: string[] = (m as any)._groups || [];
      const top = 96, bot = H - 70, x0 = W * 0.06, x1 = W * 0.94;
      const nc = groups.length;
      const colX = (col: number) => x0 + (col * (x1 - x0)) / Math.max(1, nc - 1);
      const nodeXY = (nd: N) => {
        const x = colX(nd.col);
        const pad = Math.min(40, (bot - top) / (nd.n + 1));
        const y = nd.n <= 1 ? (top + bot) / 2 : top + pad + (nd.idx * (bot - top - 2 * pad)) / (nd.n - 1);
        return [x, y];
      };
      const rad = Math.max(7, Math.min(15, (bot - top) / (13 * 2.3)));

      // decay activity
      m.forEach((nd) => { nd.act = Math.max(0.16, nd.act * 0.965); });

      // static edges (faint)
      ctx.lineCap = "round";
      for (const e of edges.current) {
        const a = m.get(e.s)!, b = m.get(e.t)!;
        const [ax, ay] = nodeXY(a), [bx, by] = nodeXY(b);
        const cx = (ax + bx) / 2, cy = (ay + by) / 2 - (bx - ax) * 0.06;
        ctx.strokeStyle = "rgba(120,170,200,0.14)"; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.quadraticCurveTo(cx, cy, bx, by); ctx.stroke();
      }

      // ambient baseline flow (always-on) + fire-event bursts (real activity)
      ctx.globalCompositeOperation = "lighter";
      const eg = edges.current;
      for (let ei = 0; ei < eg.length; ei++) {
        const a = m.get(eg[ei].s), b = m.get(eg[ei].t);
        if (!a || !b) continue;
        const [ax, ay] = nodeXY(a), [bx, by] = nodeXY(b);
        const cx = (ax + bx) / 2, cy = (ay + by) / 2 - (bx - ax) * 0.06;
        const speed = 0.06 + ((ei * 37) % 11) / 120;
        for (let q = 0; q < 2; q++) {
          const tt = ((ts / 1000) * speed + ei * 0.137 + q * 0.5) % 1;
          const it = 1 - tt;
          const x = it * it * ax + 2 * it * tt * cx + tt * tt * bx;
          const y = it * it * ay + 2 * it * tt * cy + tt * tt * by;
          const al = Math.sin(Math.PI * tt) * 0.6;
          const gr = ctx.createRadialGradient(x, y, 0, x, y, 6);
          gr.addColorStop(0, `rgba(90,200,225,${al})`);
          gr.addColorStop(1, "rgba(90,200,225,0)");
          ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.fill();
        }
      }

      const now = ts;
      parts.current = parts.current.filter((p) => {
        const a = m.get(p.s), b = m.get(p.t);
        if (!a || !b) return false;
        const tt = (now - p.born) / p.life;
        if (tt >= 1) return false;
        const [ax, ay] = nodeXY(a), [bx, by] = nodeXY(b);
        const cx = (ax + bx) / 2, cy = (ay + by) / 2 - (bx - ax) * 0.06;
        const it = 1 - tt;
        const x = it * it * ax + 2 * it * tt * cx + tt * tt * bx;
        const y = it * it * ay + 2 * it * tt * cy + tt * tt * by;
        const al = Math.sin(Math.PI * tt);
        const sz = 2.6 * (0.5 + 0.5 * al);
        const gr = ctx.createRadialGradient(x, y, 0, x, y, sz * 3.2);
        gr.addColorStop(0, `rgba(${p.col[0]},${p.col[1]},${p.col[2]},${0.95 * al})`);
        gr.addColorStop(1, `rgba(${p.col[0]},${p.col[1]},${p.col[2]},0)`);
        ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, sz * 3.2, 0, Math.PI * 2); ctx.fill();
        return true;
      });
      ctx.globalCompositeOperation = "source-over";

      // nodes
      m.forEach((nd) => {
        const [x, y] = nodeXY(nd);
        const tw = 0.06 * (0.5 + 0.5 * Math.sin(ts / 1000 * 1.6 + nd.idx * 0.7 + nd.col));
        const v = Math.max(0, Math.min(1, nd.act + tw));
        if (v > 0.3) {
          const hg = ctx.createRadialGradient(x, y, rad * 0.4, x, y, rad * 3);
          hg.addColorStop(0, `rgba(120,200,255,${0.28 * v})`); hg.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = hg; ctx.beginPath(); ctx.arc(x, y, rad * 3, 0, Math.PI * 2); ctx.fill();
        }
        const lum = Math.round(45 + 200 * v);
        const g = ctx.createRadialGradient(x - rad * 0.3, y - rad * 0.3, rad * 0.2, x, y, rad);
        g.addColorStop(0, `rgb(${lum + 20},${lum + 25},${lum + 35})`);
        g.addColorStop(1, `rgb(${Math.round(lum * 0.5)},${Math.round(lum * 0.55)},${Math.round(lum * 0.65)})`);
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fill();
        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(200,225,255,0.4)"; ctx.stroke();
      });

      // column labels
      ctx.textAlign = "center"; ctx.font = "11px ui-monospace,monospace";
      groups.forEach((g, ci) => {
        ctx.fillStyle = "rgba(150,220,200,0.8)";
        ctx.fillText(SHORT[g] || g.toUpperCase(), colX(ci), top - 26);
      });
      ctx.textAlign = "left";
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={cvs} style={{ position: "fixed", inset: 0, zIndex: 100, display: "block", background: "#04070a" }} />;
}
