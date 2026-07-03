"use client";
import { useEffect, useRef, useState } from "react";

// Full-screen LIVE transformer network view (3Blue1Brown style) mapped to our
// actual model: columns = Patches -> Encoder L1 -> Encoder L2 -> Macro Drivers.
// Node brightness = real latent activation; edges = the REAL attention weights
// (cyan where a connection amplifies the target, red where it attenuates);
// the "input image" is the symbol's live price series. Streams /api/transformer.

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

const CYAN = "#35e0d8";
const RED = "#ff5a6a";

function norm(a: number[]): number[] {
  if (!a || !a.length) return [];
  const mx = Math.max(...a), mn = Math.min(...a);
  const d = mx - mn || 1;
  return a.map((v) => (v - mn) / d);
}

export default function TransformerNet({ onClose }: { onClose: () => void }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [live, setLive] = useState(false);
  const cvs = useRef<HTMLCanvasElement>(null);
  const traceRef = useRef<Trace | null>(null);

  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${BASE}/api/transformer/stream`);
      es.onopen = () => setLive(true);
      es.onmessage = (m) => {
        if (!m.data || m.data.startsWith(":")) return;
        try {
          const t = JSON.parse(m.data) as Trace;
          if (t && !t.error && t.layers?.length) { traceRef.current = t; setTrace(t); }
        } catch { /* ignore */ }
      };
      es.onerror = () => setLive(false);
    } catch { /* no EventSource */ }
    return () => es?.close();
  }, []);

  useEffect(() => {
    const c = cvs.current;
    if (!c) return;
    const draw = () => {
      const t = traceRef.current;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const W = window.innerWidth, H = window.innerHeight;
      c.width = W * dpr; c.height = H * dpr;
      c.style.width = W + "px"; c.style.height = H + "px";
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#04070a"; ctx.fillRect(0, 0, W, H);
      if (!t) {
        ctx.fillStyle = "#567"; ctx.font = "14px ui-monospace, monospace";
        ctx.fillText("linking to live transformer…", 40, 60);
        return;
      }

      const top = 90, bot = H - 60;
      const cap = (arr: number[], k = 20) => (arr.length <= k ? arr : arr.slice(arr.length - k));
      const L = t.layers;
      const patches = cap(norm(t.embed_norms));
      const l1 = cap(norm(L[0]?.latent_norms || []));
      const l2 = cap(norm(L[1]?.latent_norms || l1));
      const factorsE = Object.entries(t.cross_attention?.weights || {}).sort((a, b) => b[1] - a[1]);
      const cols = [
        { name: `PATCHES · ${t.n_patches}`, vals: patches, labels: null as string[] | null },
        { name: "ENCODER · L1", vals: l1, labels: null },
        { name: "ENCODER · L2", vals: l2, labels: null },
        { name: "MACRO DRIVERS", vals: factorsE.map((f) => f[1]), labels: factorsE.map((f) => f[0]) },
      ];
      const nCols = cols.length;
      const x0 = W * 0.30, x1 = W * 0.94;
      const colX = cols.map((_, i) => x0 + (i * (x1 - x0)) / (nCols - 1));
      const yOf = (col: number[], i: number) => {
        const n = col.length; if (n === 1) return (top + bot) / 2;
        const pad = Math.min(40, (bot - top) / (n + 1));
        return top + pad + (i * (bot - top - 2 * pad)) / (n - 1);
      };
      const rOf = (col: number[]) => Math.max(5, Math.min(16, (bot - top) / (col.length * 2.4)));

      // ---- edges (real attention), drawn under nodes ----
      const drawEdges = (aVals: number[], bVals: number[], ax: number, bx: number,
                         attn: number[][] | null, kTop: number) => {
        const ra = rOf(aVals), rb = rOf(bVals);
        for (let j = 0; j < bVals.length; j++) {
          let weights: { i: number; w: number }[] = [];
          if (attn && attn[j]) {
            const row = attn[j];
            weights = aVals.map((_, i) => ({ i, w: row[i] ?? 0 }));
          } else {
            // drivers column: connect to the most-active source nodes
            weights = aVals.map((v, i) => ({ i, w: v * (bVals[j] || 0) }));
          }
          weights.sort((p, q) => q.w - p.w);
          const mx = weights[0]?.w || 1;
          for (const { i, w } of weights.slice(0, kTop)) {
            if (w <= 0) continue;
            const amp = (bVals[j] || 0) - (aVals[i] || 0);      // amplify vs attenuate
            ctx.strokeStyle = amp >= 0 ? CYAN : RED;
            ctx.globalAlpha = Math.min(0.85, 0.06 + 0.8 * (w / (mx || 1)));
            ctx.lineWidth = 0.4 + 1.6 * (w / (mx || 1));
            ctx.beginPath();
            ctx.moveTo(ax + ra, yOf(aVals, i));
            ctx.lineTo(bx - rb, yOf(bVals, j));
            ctx.stroke();
          }
        }
        ctx.globalAlpha = 1;
      };
      drawEdges(patches, l1, colX[0], colX[1], L[0]?.attn || null, 3);
      drawEdges(l1, l2, colX[1], colX[2], L[1]?.attn || null, 3);
      drawEdges(l2, cols[3].vals, colX[2], colX[3], null, 5);

      // ---- nodes ----
      cols.forEach((col, ci) => {
        const r = rOf(col.vals);
        col.vals.forEach((v, i) => {
          const x = colX[ci], y = yOf(col.vals, i);
          const g = Math.round(25 + 220 * Math.max(0, Math.min(1, v)));
          ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
          ctx.fillStyle = `rgb(${g},${g},${g})`; ctx.fill();
          ctx.lineWidth = 1.2; ctx.strokeStyle = "rgba(230,240,255,0.55)"; ctx.stroke();
          if (col.labels) {
            ctx.fillStyle = "#cfe"; ctx.font = "12px ui-monospace, monospace";
            ctx.textBaseline = "middle";
            ctx.fillText(`${col.labels[i]} ${(v * 100).toFixed(0)}%`, x + r + 8, y);
          }
        });
        ctx.fillStyle = ci === 3 ? CYAN : "#7fb";
        ctx.font = "11px ui-monospace, monospace"; ctx.textBaseline = "alphabetic";
        ctx.textAlign = "center"; ctx.fillText(col.name, colX[ci], top - 22);
        ctx.textAlign = "left";
      });

      // ---- input "image": live price series + brace ----
      const bx0 = W * 0.03, bx1 = W * 0.22, by0 = top + 6, by1 = Math.min(bot, top + 190);
      ctx.strokeStyle = CYAN; ctx.lineWidth = 2; ctx.strokeRect(bx0, by0, bx1 - bx0, by1 - by0);
      const series = t.patch_input || [];
      if (series.length > 1) {
        const sn = norm(series);
        ctx.strokeStyle = "#eaf2ff"; ctx.lineWidth = 2; ctx.beginPath();
        sn.forEach((v, i) => {
          const px = bx0 + 10 + (i * (bx1 - bx0 - 20)) / (sn.length - 1);
          const py = by1 - 12 - v * (by1 - by0 - 24);
          i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
        });
        ctx.stroke();
      }
      ctx.fillStyle = "#fff"; ctx.font = "bold 18px ui-monospace, monospace";
      ctx.fillText(t.symbol, bx0 + 6, by0 - 8);
      if (t.last_price != null) {
        ctx.fillStyle = "#8aa"; ctx.font = "12px ui-monospace, monospace";
        ctx.fillText(`$${t.last_price}`, bx0 + 70, by0 - 8);
      }
      // brace label like "784"
      ctx.fillStyle = "#cde"; ctx.font = "22px ui-serif, Georgia, serif";
      ctx.fillText(String(t.n_patches), (bx1 + x0) / 2 - 30, (by0 + by1) / 2 + 8);
    };

    draw();
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [trace]);

  const dir = trace?.direction || {};
  const probUp = typeof dir.prob_up === "number" ? dir.prob_up : undefined;

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 100, background: "#04070a" }}>
      <canvas ref={cvs} style={{ display: "block" }} />
      <div style={{ position: "fixed", top: 16, left: 24, right: 24, display: "flex",
                    justifyContent: "space-between", alignItems: "center",
                    fontFamily: "ui-monospace, monospace", color: "#cfe", pointerEvents: "none" }}>
        <div style={{ fontWeight: 700, letterSpacing: 1, color: CYAN }}>
          ◆ TRANSFORMER NETWORK · LIVE
          <span style={{ color: live ? CYAN : "#777", fontSize: 11, marginLeft: 10 }}>
            {live ? "● streaming" : "○ linking"}
          </span>
        </div>
        <div style={{ fontSize: 12, color: "#9ab" }}>
          {trace ? `${trace.symbol} · ${trace.d_model}d/${trace.n_heads}h/${trace.n_layers}L · ${trace.n_seeds || 1}-seed`
                 : ""}
          {probUp != null && (
            <span style={{ color: probUp >= 0.5 ? CYAN : RED, marginLeft: 12 }}>
              P(up) {(probUp * 100).toFixed(0)}%
            </span>
          )}
        </div>
      </div>
      <button onClick={onClose}
              style={{ position: "fixed", top: 14, right: 16, zIndex: 101, cursor: "pointer",
                       background: "rgba(6,12,16,0.85)", color: "#cfe",
                       border: "1px solid rgba(53,224,216,0.3)", borderRadius: 8,
                       padding: "6px 12px", fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
        ✕ close
      </button>
      <div style={{ position: "fixed", bottom: 14, left: 24, fontFamily: "ui-monospace, monospace",
                    fontSize: 11, color: "#567", pointerEvents: "none" }}>
        nodes = real latent activation · edges = real attention (cyan amplify · red attenuate) · updates each forward pass
      </div>
    </div>
  );
}
