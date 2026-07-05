"use client";
import { useEffect, useRef, useState } from "react";

// Live self-improvement dashboard. Polls /api/learning and shows the autonomy
// policy + kill switch, the drawdown circuit-breaker, the ML improvement curve
// (AUC/edge over successive retrains), and recent autonomous actions + why.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "http://127.0.0.1:8000";

type MlPoint = { trained_at?: string; auc?: number; edge?: number; acc?: number; promoted?: boolean };
type Audit = { action?: string; status?: string; reason?: string };
type Learning = {
  autonomy?: { policy?: { mode?: string; kill_switch?: boolean }; recent?: Audit[]; actions?: any[] };
  breaker?: { tripped?: boolean; dd?: number; eq?: number; hw?: number };
  ml_history?: MlPoint[];
  risk?: { aggression?: number; edge?: number; equity?: number; drawdown?: number;
           aggression_reco?: number | null; equity_history?: [number, number][] };
  hypotheses?: { baseline_vs_benchmark?: number; winner?: boolean; promoted?: any; leaderboard?: any[] };
};

const CYAN = "#35e0d8";
const AMBER = "#f2a53a";
const RED = "#ff5a6a";

export default function LearningPanel() {
  const [d, setD] = useState<Learning | null>(null);
  const [open, setOpen] = useState(true);
  const curveRef = useRef<HTMLCanvasElement>(null);
  const eqRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${BASE}/api/learning`, { cache: "no-store" });
        if (r.ok && alive) setD(await r.json());
      } catch {
        /* transient */
      }
    };
    pull();
    const t = setInterval(pull, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    const c = curveRef.current;
    const h = d?.ml_history || [];
    if (!c || h.length < 2) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = 268, ht = 60;
    c.width = w; c.height = ht;
    ctx.clearRect(0, 0, w, ht);
    const draw = (key: "auc" | "edge", color: string, lo: number, hi: number) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
      h.forEach((p, i) => {
        const v = (p[key] ?? lo);
        const x = (i / (h.length - 1)) * (w - 4) + 2;
        const y = ht - ((v - lo) / (hi - lo)) * (ht - 6) - 3;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    };
    // AUC baseline 0.5
    ctx.strokeStyle = "rgba(255,255,255,0.12)"; ctx.beginPath();
    const y50 = ht - ((0.5 - 0.4) / (0.65 - 0.4)) * (ht - 6) - 3;
    ctx.moveTo(2, y50); ctx.lineTo(w - 2, y50); ctx.stroke();
    draw("auc", CYAN, 0.4, 0.65);
    draw("edge", AMBER, -0.1, 0.15);
  }, [d]);

  // P&L equity sparkline -- real paper-account equity over time from the breaker log
  useEffect(() => {
    const c = eqRef.current;
    const pts = (d?.risk?.equity_history || []).map((p) => Number(p[1])).filter((v) => v > 0);
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = 268, ht = 46;
    c.width = w; c.height = ht;
    ctx.clearRect(0, 0, w, ht);
    if (pts.length < 2) return;
    const lo = Math.min(...pts), hi = Math.max(...pts);
    const span = hi - lo || 1;
    const up = pts[pts.length - 1] >= pts[0];
    const col = up ? CYAN : RED;
    const xy = (v: number, i: number): [number, number] => [
      (i / (pts.length - 1)) * (w - 4) + 2,
      ht - ((v - lo) / span) * (ht - 8) - 4,
    ];
    ctx.beginPath();
    pts.forEach((v, i) => { const [x, y] = xy(v, i); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.lineTo(w - 2, ht); ctx.lineTo(2, ht); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, ht);
    g.addColorStop(0, up ? "rgba(53,224,216,0.28)" : "rgba(255,90,106,0.28)");
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g; ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.beginPath();
    pts.forEach((v, i) => { const [x, y] = xy(v, i); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
    const [dx, dy] = xy(pts[pts.length - 1], pts.length - 1);
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(dx, dy, 2.4, 0, Math.PI * 2); ctx.fill();
  }, [d]);

  if (!open) {
    return <button onClick={() => setOpen(true)} style={btn}>◆ SELF-IMPROVEMENT</button>;
  }

  const pol = d?.autonomy?.policy || {};
  const mode = (pol.mode || "…").toUpperCase();
  const kill = pol.kill_switch;
  const br = d?.breaker || {};
  const hist = d?.ml_history || [];
  const last = hist[hist.length - 1] || {};
  const audits = (d?.autonomy?.recent || []).slice(0, 5);
  const modeColor = kill ? RED : mode === "AUTO" ? CYAN : AMBER;
  const hypo = d?.hypotheses;

  return (
    <div style={panel}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, letterSpacing: 1, color: CYAN, fontSize: 12 }}>◆ SELF-IMPROVEMENT</div>
        <button onClick={() => setOpen(false)} style={x}>×</button>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 8, fontSize: 10 }}>
        <span style={{ ...badge, color: modeColor, borderColor: modeColor }}>
          {kill ? "HALTED" : `AUTONOMY: ${mode}`}
        </span>
        <span style={{ color: (br.tripped ? RED : "#8aa") }}>
          breaker {typeof br.dd === "number" ? `${br.dd}% dd` : "—"}
        </span>
      </div>

      {d?.risk && (
        <>
          <div style={{ ...sec, marginTop: 2 }}>AGGRESSION · EDGE · EQUITY</div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: 9, color: "#9ab", width: 30 }}>AGGR</span>
            <div style={{ ...barTrack, flex: 1 }}>
              <div style={{ ...barFill, width: `${Math.min(100, ((d.risk.aggression ?? 0) / 1.2) * 100)}%`,
                            background: (d.risk.aggression ?? 0) > 0.8 ? AMBER : CYAN }} />
            </div>
            <span style={{ fontSize: 10, color: "#cfe", width: 28, textAlign: "right" }}>{(d.risk.aggression ?? 0).toFixed(2)}</span>
          </div>
          {d.risk.aggression_reco != null && (
            <div style={{ fontSize: 9, color: "#7a9", marginBottom: 4 }}>
              lab target {Number(d.risk.aggression_reco).toFixed(2)} - autonomy blends toward it
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#8aa", marginBottom: 8 }}>
            <span style={{ color: (d.risk.edge ?? 0) > 0 ? CYAN : RED }}>edge {(d.risk.edge ?? 0).toFixed(3)}</span>
            <span>eq ${d.risk.equity != null ? Math.round(d.risk.equity).toLocaleString() : "—"}</span>
            <span style={{ color: (d.risk.drawdown ?? 0) > 3 ? RED : "#8aa" }}>dd {d.risk.drawdown ?? 0}%</span>
          </div>
          {(d.risk.equity_history?.length ?? 0) >= 2 && (
            <>
              <div style={sec}>P&amp;L CURVE (paper equity)</div>
              <canvas ref={eqRef} style={{ width: "100%", height: 46, borderRadius: 4, marginBottom: 8 }} />
            </>
          )}
        </>
      )}
      <div style={sec}>ML IMPROVEMENT (AUC ▏ edge)</div>
      <canvas ref={curveRef} style={{ width: "100%", height: 60, borderRadius: 4 }} />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#8aa", marginTop: 2 }}>
        <span style={{ color: CYAN }}>AUC {last.auc ?? "—"}</span>
        <span style={{ color: AMBER }}>edge {last.edge ?? "—"}</span>
        <span>{hist.length} retrains</span>
      </div>

      <div style={{ ...sec, marginTop: 10 }}>RECENT AUTONOMOUS ACTIONS</div>
      {audits.length === 0 && <div style={{ fontSize: 10, color: "#678" }}>none yet</div>}
      {audits.map((a, i) => (
        <div key={i} style={{ fontSize: 10, marginBottom: 3, color: "#bcd" }}>
          <span style={{ color: a.status === "applied" ? CYAN : "#889" }}>
            {a.status === "applied" ? "✓" : "•"} {a.action}
          </span>
          <span style={{ color: "#788" }}> — {(a.reason || "").slice(0, 60)}</span>
        </div>
      ))}

      {hypo && (hypo.leaderboard || []).length > 0 && (
        <>
          <div style={{ ...sec, marginTop: 10 }}>
            STRATEGY DISCOVERY {hypo.promoted ? <span style={{ color: CYAN }}>· PROMOTED</span> : <span style={{ color: "#788" }}>· testing</span>}
          </div>
          {(hypo.leaderboard || []).slice(0, 3).map((r: any, i: number) => (
            <div key={i} style={{ fontSize: 10, marginBottom: 2, color: r.name === "baseline" ? "#889" : "#bcd" }}>
              <span style={{ color: (r.vs_benchmark ?? 0) > 0 ? CYAN : RED }}>
                {(r.vs_benchmark >= 0 ? "+" : "")}{(r.vs_benchmark ?? 0).toFixed?.(3)}
              </span>
              <span> {String(r.name).slice(0, 22)}</span>
              <span style={{ color: "#677" }}> ({r.trades}t)</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

const panel: React.CSSProperties = {
  position: "fixed", left: 18, top: 344, width: 300, padding: 15,
  background: "linear-gradient(158deg, rgba(9,15,26,0.94), rgba(7,12,22,0.86))",
  border: "1px solid rgba(96,214,230,0.28)", borderRadius: 12,
  backdropFilter: "blur(18px) saturate(1.2)", WebkitBackdropFilter: "blur(18px) saturate(1.2)",
  color: "#e9f2f8", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", zIndex: 50,
  boxShadow: "0 18px 60px rgba(0,0,0,0.55), 0 0 0 1px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.05)",
};
const btn: React.CSSProperties = {
  position: "fixed", left: 18, top: 344, zIndex: 50, cursor: "pointer",
  background: "linear-gradient(180deg, rgba(60,240,228,0.12), rgba(60,240,228,0.04))",
  color: CYAN, border: "1px solid rgba(96,214,230,0.28)",
  borderRadius: 9, padding: "7px 12px", fontSize: 11, letterSpacing: 1, fontFamily: "ui-monospace, monospace",
  backdropFilter: "blur(12px)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
};
const x: React.CSSProperties = { cursor: "pointer", background: "transparent", color: "#889", border: "none", fontSize: 16, lineHeight: 1 };
const sec: React.CSSProperties = { fontSize: 9, letterSpacing: 2, textTransform: "uppercase", color: "#57cdc6", marginBottom: 5, marginTop: 2 };
const barTrack: React.CSSProperties = { height: 6, background: "rgba(140,180,210,0.10)", borderRadius: 4, overflow: "hidden" };
const barFill: React.CSSProperties = { height: "100%", borderRadius: 4, boxShadow: "0 0 10px rgba(60,240,228,0.5)" };
const badge: React.CSSProperties = { border: "1px solid", borderRadius: 6, padding: "2px 8px", letterSpacing: 0.5, fontWeight: 600 };
