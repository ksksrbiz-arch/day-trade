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
  risk?: { aggression?: number; edge?: number; equity?: number; drawdown?: number };
  hypotheses?: { baseline_vs_benchmark?: number; winner?: boolean; promoted?: any; leaderboard?: any[] };
};

const CYAN = "#35e0d8";
const AMBER = "#f2a53a";
const RED = "#ff5a6a";

export default function LearningPanel() {
  const [d, setD] = useState<Learning | null>(null);
  const [open, setOpen] = useState(true);
  const curveRef = useRef<HTMLCanvasElement>(null);

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
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#8aa", marginBottom: 8 }}>
            <span style={{ color: (d.risk.edge ?? 0) > 0 ? CYAN : RED }}>edge {(d.risk.edge ?? 0).toFixed(3)}</span>
            <span>eq ${d.risk.equity != null ? Math.round(d.risk.equity).toLocaleString() : "—"}</span>
            <span style={{ color: (d.risk.drawdown ?? 0) > 3 ? RED : "#8aa" }}>dd {d.risk.drawdown ?? 0}%</span>
          </div>
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
  position: "fixed", left: 16, top: 320, width: 300, padding: 14,
  background: "rgba(6,12,16,0.82)", border: "1px solid rgba(53,224,216,0.22)",
  borderRadius: 10, backdropFilter: "blur(8px)", color: "#cfe",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", zIndex: 50,
  boxShadow: "0 0 40px rgba(0,0,0,0.5)",
};
const btn: React.CSSProperties = {
  position: "fixed", left: 16, top: 320, zIndex: 50, cursor: "pointer",
  background: "rgba(6,12,16,0.82)", color: CYAN, border: "1px solid rgba(53,224,216,0.22)",
  borderRadius: 8, padding: "6px 10px", fontSize: 11, letterSpacing: 1, fontFamily: "ui-monospace, monospace",
};
const x: React.CSSProperties = { cursor: "pointer", background: "transparent", color: "#889", border: "none", fontSize: 16, lineHeight: 1 };
const sec: React.CSSProperties = { fontSize: 9, letterSpacing: 1, color: "#6a8", marginBottom: 4 };
const barTrack: React.CSSProperties = { height: 6, background: "rgba(255,255,255,0.06)", borderRadius: 3, overflow: "hidden" };
const barFill: React.CSSProperties = { height: "100%", borderRadius: 3 };
const badge: React.CSSProperties = { border: "1px solid", borderRadius: 4, padding: "1px 6px", letterSpacing: 0.5 };
