"use client";
import { useEffect, useRef } from "react";

// Beautiful, ANIMATED full-screen transformer dashboard. Continuous 60fps:
// glowing particles travel along the synapses (speed + density = real attention
// weight), neuron brightness smoothly lerps between forward passes, active
// neurons pulse. Columns = Patches -> Encoder L1 -> Encoder L2 -> Macro Drivers,
// all driven by the live /api/transformer stream. Nothing pre-baked.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "http://127.0.0.1:8000";

type Layer = { attn: number[][]; entropy: number; latent_norms: number[] };
type Trace = {
  symbol: string; last_price?: number; n_patches: number; d_model: number;
  n_heads: number; n_layers: number; layers: Layer[]; embed_norms: number[];
  patch_input: number[]; pooled_norm: number; n_seeds?: number;
  cross_attention: { weights: Record<string, number>; dominant: string | null };
  attention_rollout?: number[]; entropy_by_layer: number[];
  direction?: { prob_up?: number; bias?: string } & Record<string, unknown>;
  error?: string;
};

type Edge = { c: number; s: number; t: number; w: number; up: boolean };
type Snap = {
  symbol: string; price?: number; dims: string; seeds: number; probUp?: number;
  cols: number[][];               // per-column activations [0..1]
  labels: (string[] | null)[];    // driver labels
  edges: Edge[];                  // topology (index pairs)
  series: number[];               // input price series (normalized)
  dominant: string | null;
};

const CY = [53, 224, 216];
const RD = [255, 110, 122];
const rgba = (c: number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function norm(a: number[]): number[] {
  if (!a || !a.length) return [];
  const mx = Math.max(...a), mn = Math.min(...a), d = mx - mn || 1;
  return a.map((v) => (v - mn) / d);
}
const capTail = (a: number[], k = 22) => (a.length <= k ? a : a.slice(a.length - k));

function buildSnap(t: Trace): Snap {
  const L = t.layers || [];
  const patches = capTail(norm(t.embed_norms || []));
  const l1 = capTail(norm(L[0]?.latent_norms || []));
  const l2 = capTail(norm(L[1]?.latent_norms || l1));
  const fe = Object.entries(t.cross_attention?.weights || {}).sort((a, b) => b[1] - a[1]);
  const drivers = fe.map((f) => f[1]);
  const cols = [patches, l1, l2, drivers];
  const labels = [null, null, null, fe.map((f) => f[0])];
  const edges: Edge[] = [];
  const topK = (row: number[], k: number) =>
    row.map((w, i) => ({ i, w })).sort((a, b) => b.w - a.w).slice(0, k).filter((e) => e.w > 0);
  const link = (ci: number, aVals: number[], bVals: number[], attn: number[][] | null, k: number) => {
    for (let j = 0; j < bVals.length; j++) {
      const row = attn && attn[j] ? attn[j] : aVals.map((v) => v * (bVals[j] || 0));
      const mx = Math.max(1e-9, ...row);
      for (const { i, w } of topK(row, k)) {
        edges.push({ c: ci, s: i, t: j, w: w / mx, up: (bVals[j] || 0) >= (aVals[i] || 0) });
      }
    }
  };
  link(0, patches, l1, L[0]?.attn || null, 3);
  link(1, l1, l2, L[1]?.attn || null, 3);
  link(2, l2, drivers, null, 4);
  return {
    symbol: t.symbol, price: t.last_price, dims: `${t.d_model}d·${t.n_heads}h·${t.n_layers}L`,
    seeds: t.n_seeds || 1, probUp: typeof t.direction?.prob_up === "number" ? t.direction!.prob_up : undefined,
    cols, labels, edges, series: norm(t.patch_input || []), dominant: t.cross_attention?.dominant || null,
  };
}

export default function TransformerNet() {
  const cvs = useRef<HTMLCanvasElement>(null);
  const target = useRef<Snap | null>(null);   // latest data
  const shown = useRef<number[][]>([]);         // smoothed activations

  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${BASE}/api/transformer/stream`);
      es.onmessage = (m) => {
        if (!m.data || m.data.startsWith(":")) return;
        try {
          const t = JSON.parse(m.data) as Trace;
          if (t && !t.error && t.layers?.length) target.current = buildSnap(t);
        } catch { /* ignore */ }
      };
      es.onerror = () => {};
    } catch { /* no EventSource */ }
    return () => es?.close();
  }, []);

  useEffect(() => {
    const c = cvs.current; if (!c) return;
    let raf = 0;
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    const layout = (W: number, H: number, snap: Snap) => {
      const top = 96, bot = H - 70;
      const x0 = W * 0.30, x1 = W * 0.93;
      const nc = snap.cols.length;
      const colX = snap.cols.map((_, i) => x0 + (i * (x1 - x0)) / (nc - 1));
      const posY = (col: number[], i: number) => {
        const n = col.length; if (n <= 1) return (top + bot) / 2;
        const pad = Math.min(46, (bot - top) / (n + 1));
        return top + pad + (i * (bot - top - 2 * pad)) / (n - 1);
      };
      const rad = (col: number[]) => Math.max(6, Math.min(17, (bot - top) / (col.length * 2.3)));
      return { top, bot, colX, posY, rad };
    };

    const frame = (ts: number) => {
      const snap = target.current;
      const W = window.innerWidth, H = window.innerHeight;
      if (c.width !== W * dpr) { c.width = W * dpr; c.height = H * dpr; c.style.width = W + "px"; c.style.height = H + "px"; }
      const ctx = c.getContext("2d"); if (!ctx) { raf = requestAnimationFrame(frame); return; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // background
      const bg = ctx.createRadialGradient(W * 0.5, H * 0.45, 80, W * 0.5, H * 0.5, Math.max(W, H) * 0.7);
      bg.addColorStop(0, "#0a1016"); bg.addColorStop(1, "#04070a");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

      if (!snap) { ctx.fillStyle = "#567"; ctx.font = "14px ui-monospace,monospace"; ctx.fillText("linking to live transformer…", 40, 70); raf = requestAnimationFrame(frame); return; }

      // smooth activations toward target
      if (shown.current.length !== snap.cols.length || shown.current.some((c2, i) => c2.length !== snap.cols[i].length)) {
        shown.current = snap.cols.map((col) => col.slice());
      } else {
        for (let ci = 0; ci < snap.cols.length; ci++)
          for (let i = 0; i < snap.cols[ci].length; i++)
            shown.current[ci][i] += (snap.cols[ci][i] - shown.current[ci][i]) * 0.06;
      }
      const A = shown.current;
      const { top, bot, colX, posY, rad } = layout(W, H, snap);
      const time = ts / 1000;

      // ---------- edges (faint bezier synapses) ----------
      ctx.lineCap = "round";
      const ctrlOf = (ax: number, ay: number, bx: number, by: number) => {
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        return [mx, my - (bx - ax) * 0.10];
      };
      for (const e of snap.edges) {
        const av = A[e.c], bv = A[e.c + 1];
        if (!av || !bv) continue;
        const ax = colX[e.c] + rad(av), ay = posY(av, e.s);
        const bx = colX[e.c + 1] - rad(bv), by = posY(bv, e.t);
        const [cx, cy] = ctrlOf(ax, ay, bx, by);
        const col = e.up ? CY : RD;
        ctx.strokeStyle = rgba(col, 0.04 + 0.10 * e.w);
        ctx.lineWidth = 0.5 + 1.4 * e.w;
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.quadraticCurveTo(cx, cy, bx, by); ctx.stroke();
      }

      // ---------- traveling particles (the "live nodes") ----------
      ctx.globalCompositeOperation = "lighter";
      for (const e of snap.edges) {
        const av = A[e.c], bv = A[e.c + 1];
        if (!av || !bv) continue;
        const ax = colX[e.c] + rad(av), ay = posY(av, e.s);
        const bx = colX[e.c + 1] - rad(bv), by = posY(bv, e.t);
        const [cx, cy] = ctrlOf(ax, ay, bx, by);
        const col = e.up ? CY : RD;
        const count = 1 + Math.round(e.w * 2.4);          // more traffic on strong edges
        const speed = 0.10 + e.w * 0.45;
        for (let p = 0; p < count; p++) {
          const tt = ((time * speed + p / count + (e.s * 0.13 + e.t * 0.07)) % 1);
          const it = 1 - tt;
          const x = it * it * ax + 2 * it * tt * cx + tt * tt * bx;
          const y = it * it * ay + 2 * it * tt * cy + tt * tt * by;
          const a = Math.sin(Math.PI * tt);                // fade in/out along the path
          const sz = (1.4 + 2.2 * e.w) * (0.5 + 0.5 * a);
          const gr = ctx.createRadialGradient(x, y, 0, x, y, sz * 3);
          gr.addColorStop(0, rgba(col, 0.9 * a));
          gr.addColorStop(1, rgba(col, 0));
          ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, sz * 3, 0, Math.PI * 2); ctx.fill();
        }
      }
      ctx.globalCompositeOperation = "source-over";

      // ---------- neurons ----------
      snap.cols.forEach((_, ci) => {
        const vals = A[ci], r = rad(vals);
        vals.forEach((v, i) => {
          const x = colX[ci], y = posY(vals, i);
          const vv = Math.max(0, Math.min(1, v));
          // pulsing halo for active neurons
          if (vv > 0.45) {
            const pulse = 0.5 + 0.5 * Math.sin(time * 2.5 + i * 0.6 + ci);
            const hg = ctx.createRadialGradient(x, y, r * 0.4, x, y, r * (2.4 + pulse));
            hg.addColorStop(0, rgba(ci === 3 ? CY : [180, 210, 255], 0.22 * vv));
            hg.addColorStop(1, "rgba(0,0,0,0)");
            ctx.fillStyle = hg; ctx.beginPath(); ctx.arc(x, y, r * (2.4 + pulse), 0, Math.PI * 2); ctx.fill();
          }
          const g = ctx.createRadialGradient(x - r * 0.3, y - r * 0.3, r * 0.2, x, y, r);
          const lum = Math.round(30 + 210 * vv);
          g.addColorStop(0, `rgb(${lum + 25},${lum + 25},${lum + 30})`);
          g.addColorStop(1, `rgb(${Math.round(lum * 0.5)},${Math.round(lum * 0.5)},${Math.round(lum * 0.6)})`);
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
          ctx.lineWidth = 1.1; ctx.strokeStyle = rgba(ci === 3 ? CY : [220, 235, 255], 0.5); ctx.stroke();
          const labels = snap.labels[ci];
          if (labels) {
            ctx.fillStyle = "#dff"; ctx.font = "600 12px ui-monospace,monospace"; ctx.textBaseline = "middle";
            ctx.fillText(`${labels[i]}`, x + r + 10, y);
            ctx.fillStyle = rgba(CY, 0.8);
            ctx.fillText(`${(vv * 100).toFixed(0)}%`, x + r + 62, y);
            ctx.textBaseline = "alphabetic";
          }
        });
        const names = ["PATCHES", "ENCODER · L1", "ENCODER · L2", "MACRO DRIVERS"];
        ctx.fillStyle = ci === 3 ? rgba(CY, 0.9) : "rgba(150,220,200,0.8)";
        ctx.font = "11px ui-monospace,monospace"; ctx.textAlign = "center";
        ctx.fillText(ci === 0 ? `${names[0]} · ${snap.cols[0].length}` : names[ci], colX[ci], top - 26);
        ctx.textAlign = "left";
      });

      // ---------- input: live price series ----------
      const bx0 = W * 0.035, bx1 = W * 0.225, by0 = top + 4, by1 = Math.min(bot, top + 200);
      const box = ctx.createLinearGradient(bx0, by0, bx0, by1);
      box.addColorStop(0, "rgba(53,224,216,0.06)"); box.addColorStop(1, "rgba(53,224,216,0.01)");
      ctx.fillStyle = box; ctx.fillRect(bx0, by0, bx1 - bx0, by1 - by0);
      ctx.strokeStyle = rgba(CY, 0.5); ctx.lineWidth = 1.5; ctx.strokeRect(bx0, by0, bx1 - bx0, by1 - by0);
      const s = snap.series;
      if (s.length > 1) {
        const px = (i: number) => bx0 + 12 + (i * (bx1 - bx0 - 24)) / (s.length - 1);
        const py = (v: number) => by1 - 14 - v * (by1 - by0 - 28);
        const area = ctx.createLinearGradient(0, by0, 0, by1);
        area.addColorStop(0, "rgba(53,224,216,0.25)"); area.addColorStop(1, "rgba(53,224,216,0)");
        ctx.beginPath(); ctx.moveTo(px(0), by1 - 14);
        s.forEach((v, i) => ctx.lineTo(px(i), py(v)));
        ctx.lineTo(px(s.length - 1), by1 - 14); ctx.closePath(); ctx.fillStyle = area; ctx.fill();
        ctx.beginPath(); s.forEach((v, i) => (i ? ctx.lineTo(px(i), py(v)) : ctx.moveTo(px(i), py(v))));
        ctx.strokeStyle = "#eaf6ff"; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.fillStyle = "#fff"; ctx.font = "bold 20px ui-monospace,monospace";
      ctx.fillText(snap.symbol, bx0 + 4, by0 - 10);
      if (snap.price != null) { ctx.fillStyle = "#8ac"; ctx.font = "13px ui-monospace,monospace"; ctx.fillText(`$${snap.price}`, bx0 + 92, by0 - 10); }
      ctx.fillStyle = "rgba(210,230,245,0.85)"; ctx.font = "24px ui-serif,Georgia,serif";
      ctx.fillText(String(snap.cols[0].length), (bx1 + W * 0.30) / 2 - 34, (by0 + by1) / 2 + 8);

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={cvs} style={{ position: "fixed", inset: 0, zIndex: 100, display: "block", background: "#04070a" }} />;
}
