"use client";
import { useEffect, useState } from "react";

// The system's INTERNAL STATE, made visible: its computed "mood" (affect),
// drives, how the state is steering behaviour, and the knowledge (beliefs) it
// has built for itself. Honest: a grounded model of an internal state, not
// literal feeling -- every value maps to a measured cause on the backend.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "https://day-trade-backend.onrender.com";

const CYAN = "#3cf0e4";
const VIOLET = "#a78bfa";
const AMBER = "#ffbf4d";
const RED = "#ff5d6c";
const GREEN = "#2fe6a0";

type Psyche = {
  mood?: string; valence?: number; arousal?: number; confidence?: number;
  curiosity?: number; stress?: number;
  drives?: { explore?: number; protect?: number; exploit?: number };
  modulation?: { exploration?: number };
  beliefs?: { belief?: string; ts?: string }[];
  error?: string;
};

const MOOD_COLOR: Record<string, string> = {
  driven: CYAN, content: GREEN, curious: VIOLET, focused: "#8ea6bb",
  anxious: AMBER, subdued: "#7f93a6", stressed: RED,
};

export default function PsychePanel() {
  const [d, setD] = useState<Psyche | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${BASE}/api/psyche`, { cache: "no-store" });
        if (r.ok && alive) setD(await r.json());
      } catch {
        /* transient */
      }
    };
    pull();
    const t = setInterval(pull, 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!open) {
    const m = d?.mood ? d.mood.toUpperCase() : "MIND";
    const c = (d?.mood && MOOD_COLOR[d.mood]) || VIOLET;
    return (
      <button onClick={() => setOpen(true)} style={{ ...btn, color: c, borderColor: c + "66" }}>
        ◆ INNER STATE{d?.mood ? ` · ${m}` : ""}
      </button>
    );
  }

  const mood = d?.mood || "…";
  const mc = MOOD_COLOR[mood] || VIOLET;
  const dr = d?.drives || {};

  const bar = (label: string, v: number, color: string, signed = false) => {
    const pct = signed ? ((v + 1) / 2) * 100 : v * 100;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 4 }} key={label}>
        <span style={{ width: 62, fontSize: 9, color: "#8ea6bb", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</span>
        <div style={track}>
          {signed && <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "rgba(255,255,255,0.15)" }} />}
          <div style={{ height: "100%", width: `${Math.max(0, Math.min(100, pct))}%`, background: color, borderRadius: 4, boxShadow: `0 0 8px ${color}88` }} />
        </div>
        <span style={{ width: 34, textAlign: "right", fontSize: 10, color: "#cfe" }}>{(v ?? 0).toFixed(2)}</span>
      </div>
    );
  };

  return (
    <div style={panel}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, letterSpacing: 1, color: VIOLET, fontSize: 12 }}>◆ INNER STATE</div>
        <button onClick={() => setOpen(false)} style={x}>×</button>
      </div>

      {d?.error && <div style={{ fontSize: 10, color: RED }}>psyche offline: {d.error}</div>}

      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 9, color: "#7f93a6", letterSpacing: 1.5 }}>MOOD</span>
        <span style={{ fontSize: 17, fontWeight: 800, color: mc, textShadow: `0 0 16px ${mc}66`, textTransform: "capitalize" }}>{mood}</span>
      </div>

      <div style={sec}>AFFECT</div>
      {bar("valence", d?.valence ?? 0, (d?.valence ?? 0) >= 0 ? GREEN : RED, true)}
      {bar("arousal", d?.arousal ?? 0, AMBER)}
      {bar("confidence", d?.confidence ?? 0, CYAN)}
      {bar("curiosity", d?.curiosity ?? 0, VIOLET)}
      {bar("stress", d?.stress ?? 0, RED)}

      <div style={{ ...sec, marginTop: 8 }}>DRIVES</div>
      <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
        {([["explore", VIOLET], ["exploit", CYAN], ["protect", RED]] as [string, string][]).map(([k, c]) => (
          <div key={k} style={{ flex: 1, textAlign: "center", padding: "5px 2px", borderRadius: 7, background: c + "18", border: `1px solid ${c}44` }}>
            <div style={{ fontSize: 13, fontWeight: 800, color: c }}>{Math.round(((dr as any)[k] ?? 0) * 100)}%</div>
            <div style={{ fontSize: 8, color: "#8ea6bb", textTransform: "uppercase", letterSpacing: 0.5 }}>{k}</div>
          </div>
        ))}
      </div>

      <div style={{ ...sec, marginTop: 6 }}>SELF-BUILT BELIEFS</div>
      <div style={{ maxHeight: 150, overflow: "auto", display: "flex", flexDirection: "column", gap: 5 }}>
        {(d?.beliefs || []).length === 0 && (
          <div style={{ fontSize: 10, color: "#5a6b7d" }}>forming beliefs from experience…</div>
        )}
        {(d?.beliefs || []).slice(0, 8).map((b, i) => (
          <div key={i} style={{ fontSize: 10.5, color: "#cfe0ea", lineHeight: 1.35, paddingLeft: 9, borderLeft: `2px solid ${VIOLET}66` }}>
            {b.belief}
          </div>
        ))}
      </div>
    </div>
  );
}

const panel: React.CSSProperties = {
  position: "fixed", right: 18, top: 470, width: 300, padding: 15, zIndex: 50,
  background: "linear-gradient(158deg, rgba(9,15,26,0.94), rgba(7,12,22,0.86))",
  border: "1px solid rgba(167,139,250,0.32)", borderRadius: 12,
  backdropFilter: "blur(18px) saturate(1.2)", WebkitBackdropFilter: "blur(18px) saturate(1.2)",
  color: "#e9f2f8", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  boxShadow: "0 18px 60px rgba(0,0,0,0.55), 0 0 24px rgba(167,139,250,0.12), inset 0 1px 0 rgba(255,255,255,0.05)",
};
const btn: React.CSSProperties = {
  position: "fixed", right: 18, top: 470, zIndex: 50, cursor: "pointer",
  background: "linear-gradient(180deg, rgba(167,139,250,0.14), rgba(167,139,250,0.05))",
  border: "1px solid", borderRadius: 9, padding: "7px 12px",
  fontSize: 11, letterSpacing: 1, fontFamily: "ui-monospace, monospace",
  backdropFilter: "blur(12px)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
};
const x: React.CSSProperties = { cursor: "pointer", background: "transparent", color: "#889", border: "none", fontSize: 16, lineHeight: 1 };
const sec: React.CSSProperties = { fontSize: 9, letterSpacing: 2, textTransform: "uppercase", color: "#8b7fc6", marginBottom: 5 };
const track: React.CSSProperties = { position: "relative", flex: 1, height: 6, background: "rgba(140,180,210,0.10)", borderRadius: 4, overflow: "hidden" };
