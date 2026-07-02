"use client";
import { useEffect, useMemo, useRef, useState } from "react";

// Live transformer visualizer. Streams the REAL forward-pass trace from the
// backend (/api/transformer/stream) and renders the actual computed tensors:
// attention heatmap, per-layer entropy, cross-attention factor bars, and the
// per-patch activation strip. Nothing here is pre-baked or animated by hand.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "http://127.0.0.1:8000";

type Layer = { attn: number[][]; entropy: number; latent_norms: number[] };
type Trace = {
  symbol: string;
  ts: string;
  last_price?: number;
  n_patches: number;
  d_model: number;
  n_heads: number;
  n_layers: number;
  layers: Layer[];
  embed_norms: number[];
  patch_input: number[];
  pooled: number[];
  pooled_norm: number;
  cross_attention: { weights: Record<string, number>; dominant: string | null };
  entropy_by_layer: number[];
  attention_rollout?: number[];
  n_seeds?: number;
  direction?: { prob_up?: number; bias?: string } & Record<string, unknown>;
  error?: string;
};

const CYAN = "#35e0d8";
const AMBER = "#f2a53a";

export default function TransformerPanel() {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [layerIdx, setLayerIdx] = useState<number>(-1);
  const [open, setOpen] = useState(true);
  const [live, setLive] = useState(false);
  const heatRef = useRef<HTMLCanvasElement>(null);
  const actRef = useRef<HTMLCanvasElement>(null);
  const rollRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${BASE}/api/transformer/stream`);
      es.onopen = () => setLive(true);
      es.onmessage = (m) => {
        if (!m.data || m.data.startsWith(":")) return;
        try {
          const t = JSON.parse(m.data) as Trace;
          if (t && !t.error && t.layers && t.layers.length) setTrace(t);
        } catch {
          /* ignore malformed frame */
        }
      };
      es.onerror = () => setLive(false);
    } catch {
      /* EventSource unsupported */
    }
    return () => es?.close();
  }, []);

  const li = useMemo(() => {
    if (!trace) return 0;
    return layerIdx < 0 ? trace.layers.length - 1 : Math.min(layerIdx, trace.layers.length - 1);
  }, [trace, layerIdx]);

  // --- attention heatmap (real n×n matrix) ---
  useEffect(() => {
    const c = heatRef.current;
    if (!c || !trace || !trace.layers[li]) return;
    const A = trace.layers[li].attn;
    const n = A.length;
    const ctx = c.getContext("2d");
    if (!ctx || !n) return;
    const size = 208;
    c.width = size;
    c.height = size;
    const cell = size / n;
    let max = 1e-9;
    for (const row of A) for (const v of row) if (v > max) max = v;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const v = Math.min(1, A[i][j] / max);
        const r = Math.round(18 + v * 232);
        const g = Math.round(40 + v * 150);
        const b = Math.round(90 * (1 - v) + 40);
        ctx.fillStyle = `rgb(${r},${g},${b})`;
        ctx.fillRect(j * cell, i * cell, Math.ceil(cell), Math.ceil(cell));
      }
    }
  }, [trace, li]);

  // --- per-patch activation strip (real embed + latent norms) ---
  useEffect(() => {
    const c = actRef.current;
    if (!c || !trace) return;
    const emb = trace.embed_norms || [];
    const lat = trace.layers[li]?.latent_norms || [];
    const n = Math.max(emb.length, lat.length);
    const ctx = c.getContext("2d");
    if (!ctx || !n) return;
    const w = 268, h = 46;
    c.width = w;
    c.height = h;
    ctx.clearRect(0, 0, w, h);
    const bw = w / n;
    const mx = Math.max(1e-9, ...emb, ...lat);
    for (let i = 0; i < n; i++) {
      const e = (emb[i] || 0) / mx;
      const l = (lat[i] || 0) / mx;
      ctx.fillStyle = CYAN;
      ctx.fillRect(i * bw, h - e * h, Math.max(1, bw - 1), e * h);
      ctx.fillStyle = "rgba(242,165,58,0.55)";
      ctx.fillRect(i * bw, h - l * h, Math.max(1, bw - 1), 2);
    }
  }, [trace, li]);

  // --- attention rollout (true per-patch information flow across layers) ---
  useEffect(() => {
    const c = rollRef.current;
    const r = trace?.attention_rollout || [];
    if (!c || r.length === 0) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = 268, h = 26;
    c.width = w; c.height = h;
    ctx.clearRect(0, 0, w, h);
    const bw = w / r.length;
    const mx = Math.max(1e-9, ...r);
    for (let i = 0; i < r.length; i++) {
      const v = r[i] / mx;
      const g = Math.round(60 + v * 180);
      ctx.fillStyle = `rgb(${Math.round(30 + v * 60)},${g},${Math.round(200 - v * 40)})`;
      ctx.fillRect(i * bw, h - v * h, Math.max(1, bw - 1), v * h);
    }
  }, [trace]);

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={btnStyle}>
        ◆ TRANSFORMER
      </button>
    );
  }

  const cross = trace?.cross_attention?.weights || {};
  const crossEntries = Object.entries(cross).sort((a, b) => b[1] - a[1]);
  const ent = trace?.entropy_by_layer || [];
  const dir = trace?.direction || {};
  const probUp = typeof dir.prob_up === "number" ? dir.prob_up : undefined;

  return (
    <div style={panelStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, letterSpacing: 1, color: CYAN, fontSize: 12 }}>
          ◆ TRANSFORMER · LIVE
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: live ? CYAN : "#777" }}>
            {live ? "● streaming" : "○ linking"}
          </span>
          <button onClick={() => setOpen(false)} style={xStyle}>×</button>
        </div>
      </div>

      {!trace ? (
        <div style={{ fontSize: 11, color: "#8aa" }}>awaiting forward pass…</div>
      ) : (
        <>
          <div style={{ fontSize: 11, color: "#cfe", marginBottom: 6 }}>
            <b style={{ color: "#fff" }}>{trace.symbol}</b>
            {trace.last_price != null && <span style={{ color: "#8aa" }}> ${trace.last_price}</span>}
            <span style={{ color: "#667", float: "right" }}>
              {trace.d_model}d · {trace.n_heads}h · {trace.n_layers}L · {trace.n_patches}p
            </span>
          </div>

          {/* attention heatmap */}
          <div style={sectionLabel}>ATTENTION MAP (layer {li + 1})</div>
          <div style={{ display: "flex", gap: 8 }}>
            <canvas ref={heatRef} style={{ width: 130, height: 130, borderRadius: 4, imageRendering: "pixelated" }} />
            <div style={{ flex: 1 }}>
              <div style={sectionLabel}>LAYER ENTROPY</div>
              {ent.map((e, i) => (
                <div key={i} style={{ marginBottom: 4 }}>
                  <div style={{ fontSize: 9, color: "#8aa" }}>L{i + 1} · {e.toFixed(3)}</div>
                  <div style={barTrack}>
                    <div style={{ ...barFill, width: `${Math.min(100, e * 100)}%`, background: AMBER }} />
                  </div>
                </div>
              ))}
              <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                {trace.layers.map((_, i) => (
                  <button key={i} onClick={() => setLayerIdx(i)}
                    style={{ ...pill, borderColor: i === li ? CYAN : "#334", color: i === li ? CYAN : "#889" }}>
                    L{i + 1}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* cross-attention factor bars */}
          <div style={{ ...sectionLabel, marginTop: 10 }}>
            CROSS-ATTENTION · MACRO DRIVERS
            {trace.cross_attention?.dominant && (
              <span style={{ color: CYAN }}> · {trace.cross_attention.dominant}</span>
            )}
          </div>
          {crossEntries.length === 0 && <div style={{ fontSize: 10, color: "#678" }}>—</div>}
          {crossEntries.map(([k, v]) => (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
              <span style={{ fontSize: 9, color: "#9ab", width: 34 }}>{k}</span>
              <div style={{ ...barTrack, flex: 1 }}>
                <div style={{ ...barFill, width: `${v * 100}%`, background: CYAN }} />
              </div>
              <span style={{ fontSize: 9, color: "#8aa", width: 30, textAlign: "right" }}>{(v * 100).toFixed(0)}%</span>
            </div>
          ))}

          {/* per-patch activation strip */}
          <div style={{ ...sectionLabel, marginTop: 10 }}>PATCH ACTIVATIONS (embed ▏ latent)</div>
          <canvas ref={actRef} style={{ width: "100%", height: 46, borderRadius: 4 }} />

          <div style={{ ...sectionLabel, marginTop: 10 }}>
            ATTENTION ROLLOUT{trace.n_seeds ? ` · ${trace.n_seeds}-seed ensemble` : ""}
          </div>
          <canvas ref={rollRef} style={{ width: "100%", height: 26, borderRadius: 4 }} />

          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 10, color: "#8aa" }}>
            <span>‖pooled‖ {trace.pooled_norm}</span>
            {probUp != null && (
              <span style={{ color: probUp >= 0.5 ? CYAN : AMBER }}>
                P(up) {(probUp * 100).toFixed(0)}%
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  position: "fixed", right: 16, top: 84, width: 300, padding: 14,
  background: "rgba(6,12,16,0.82)", border: "1px solid rgba(53,224,216,0.25)",
  borderRadius: 10, backdropFilter: "blur(8px)", color: "#cfe",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", zIndex: 50,
  boxShadow: "0 0 40px rgba(0,0,0,0.5)",
};
const btnStyle: React.CSSProperties = {
  position: "fixed", right: 16, top: 84, zIndex: 50, cursor: "pointer",
  background: "rgba(6,12,16,0.82)", color: CYAN, border: "1px solid rgba(53,224,216,0.25)",
  borderRadius: 8, padding: "6px 10px", fontSize: 11, letterSpacing: 1,
  fontFamily: "ui-monospace, monospace",
};
const xStyle: React.CSSProperties = {
  cursor: "pointer", background: "transparent", color: "#889", border: "none", fontSize: 16, lineHeight: 1,
};
const sectionLabel: React.CSSProperties = { fontSize: 9, letterSpacing: 1, color: "#6a8", marginBottom: 4 };
const barTrack: React.CSSProperties = { height: 6, background: "rgba(255,255,255,0.06)", borderRadius: 3, overflow: "hidden" };
const barFill: React.CSSProperties = { height: "100%", borderRadius: 3 };
const pill: React.CSSProperties = {
  cursor: "pointer", background: "transparent", border: "1px solid #334", borderRadius: 4,
  fontSize: 9, padding: "1px 6px", fontFamily: "ui-monospace, monospace",
};
