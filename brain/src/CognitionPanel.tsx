"use client";
import { useEffect, useState } from "react";

// The free-model brain, made visible: the latest LLM market brief, the news
// catalysts it extracted, its risk-sentinel warnings, and post-mortem lessons.
// Everything here is produced by the wired-up free models (Groq / Cloudflare /
// OpenRouter) running as gated, auto-safe autonomous actions.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "https://day-trade-backend.onrender.com";

const CYAN = "#3cf0e4";
const GREEN = "#2fe6a0";
const AMBER = "#ffbf4d";
const RED = "#ff5d6c";
const VIOLET = "#a78bfa";
const DIM = "#7f93a6";

type Cog = {
  brief?: { brief?: string; ts?: string };
  catalysts?: { catalysts?: { ticker?: string; direction?: string; event?: string; confidence?: number }[]; armed?: number };
  risk?: { risk_level?: string; warnings?: string[] };
  postmortem?: { lessons?: string[]; win_rate?: number; reviewed?: number };
};

export default function CognitionPanel() {
  const [d, setD] = useState<Cog | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${BASE}/api/cognition`, { cache: "no-store" }).then((x) => x.json());
        if (alive) setD(r);
      } catch { /* transient */ }
    };
    pull();
    const t = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={{ ...btn, color: CYAN, borderColor: CYAN + "66" }}>
        ✦ STRATEGIST
      </button>
    );
  }

  const brief = d?.brief?.brief;
  const cats = d?.catalysts?.catalysts || [];
  const risk = d?.risk;
  const pm = d?.postmortem;
  const riskCol = risk?.risk_level === "high" ? RED : risk?.risk_level === "elevated" ? AMBER : GREEN;

  return (
    <div style={panel}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, letterSpacing: 1, color: CYAN, fontSize: 12 }}>✦ STRATEGIST · FREE MODELS</div>
        <button onClick={() => setOpen(false)} style={x}>×</button>
      </div>

      <div style={sec}>MARKET BRIEF</div>
      <div style={{ fontSize: 11.5, color: "#e4eef5", lineHeight: 1.45, marginBottom: 8, paddingLeft: 9, borderLeft: `2px solid ${CYAN}66` }}>
        {brief || "briefing on the next cycle…"}
        {d?.brief?.ts && <div style={{ color: DIM, fontSize: 9, marginTop: 3 }}>{d.brief.ts}</div>}
      </div>

      {cats.length > 0 && (
        <>
          <div style={sec}>NEWS CATALYSTS {typeof d?.catalysts?.armed === "number" ? `· ${d.catalysts.armed} armed` : ""}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
            {cats.slice(0, 5).map((c, i) => {
              const up = (c.direction || "").toLowerCase() === "up";
              const col = up ? GREEN : RED;
              return (
                <div key={i} style={{ fontSize: 10.5, color: "#cfe0ea", lineHeight: 1.3 }}>
                  <span style={{ color: col, fontWeight: 700 }}>{c.ticker} {up ? "▲" : "▼"}</span>{" "}
                  {(c.event || "").slice(0, 70)}
                </div>
              );
            })}
          </div>
        </>
      )}

      {risk?.warnings && risk.warnings.length > 0 && (
        <>
          <div style={{ ...sec, color: riskCol }}>RISK · {(risk.risk_level || "").toUpperCase()}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 8 }}>
            {risk.warnings.slice(0, 3).map((w, i) => (
              <div key={i} style={{ fontSize: 10, color: "#e8d6c0", lineHeight: 1.3 }}>· {w}</div>
            ))}
          </div>
        </>
      )}

      {pm?.lessons && pm.lessons.length > 0 && (
        <>
          <div style={sec}>LESSONS {typeof pm.win_rate === "number" ? `· win ${Math.round(pm.win_rate * 100)}%` : ""}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {pm.lessons.slice(0, 3).map((l, i) => (
              <div key={i} style={{ fontSize: 10, color: "#c8d6e2", lineHeight: 1.3, paddingLeft: 8, borderLeft: `2px solid ${VIOLET}55` }}>{l.slice(0, 100)}</div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

const panel: React.CSSProperties = {
  position: "fixed", left: 18, top: 132, width: 320, padding: 15, zIndex: 50,
  maxHeight: "calc(100vh - 160px)", overflowY: "auto",
  background: "linear-gradient(158deg, rgba(8,18,24,0.95), rgba(6,12,18,0.88))",
  border: "1px solid rgba(60,240,228,0.3)", borderRadius: 12,
  backdropFilter: "blur(18px) saturate(1.2)", WebkitBackdropFilter: "blur(18px) saturate(1.2)",
  color: "#e9f2f8", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  boxShadow: "0 18px 60px rgba(0,0,0,0.55), 0 0 24px rgba(60,240,228,0.1), inset 0 1px 0 rgba(255,255,255,0.05)",
};
const btn: React.CSSProperties = {
  position: "fixed", left: 18, top: 132, zIndex: 50, cursor: "pointer",
  background: "linear-gradient(180deg, rgba(60,240,228,0.14), rgba(60,240,228,0.05))",
  border: "1px solid", borderRadius: 9, padding: "7px 12px",
  fontSize: 11, letterSpacing: 1, fontFamily: "ui-monospace, monospace",
  backdropFilter: "blur(12px)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
};
const x: React.CSSProperties = { cursor: "pointer", background: "transparent", color: "#889", border: "none", fontSize: 16, lineHeight: 1 };
const sec: React.CSSProperties = { fontSize: 9, letterSpacing: 2, textTransform: "uppercase", color: "#6fbfb8", marginBottom: 5 };
