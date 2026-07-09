"use client";
import { useEffect, useState } from "react";

// Control surface for /brain: not just a visual -- this OPERATES the system.
// Reads live autonomy policy + neural-core state and writes changes back to the
// backend (CORS-open), so the brain page can drive autonomy mode, the kill
// switch, and whether the neural core influences live trades.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "https://day-trade-backend.onrender.com";

const CYAN = "#3cf0e4";
const AMBER = "#ffbf4d";
const RED = "#ff5d6c";
const VIOLET = "#a78bfa";

export default function BrainControls() {
  const [mode, setMode] = useState<string>("");
  const [kill, setKill] = useState(false);
  const [cortex, setCortex] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);

  const pull = async () => {
    try {
      const [a, c] = await Promise.all([
        fetch(`${BASE}/api/autonomy`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`${BASE}/api/cortex`, { cache: "no-store" }).then((r) => r.json()),
      ]);
      const pol = a?.policy || a || {};
      setMode(String(pol.mode || "").toLowerCase());
      setKill(!!pol.kill_switch);
      setCortex(!!c?.enabled);
    } catch {
      /* transient */
    }
  };

  useEffect(() => {
    pull();
    const t = setInterval(pull, 10000);
    return () => clearInterval(t);
  }, []);

  const post = async (path: string, body: any) => {
    setBusy(true);
    try {
      await fetch(`${BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      /* transient */
    } finally {
      setBusy(false);
      pull();
    }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={reopen}>◆ CONTROLS</button>
    );
  }

  const seg = (label: string, val: string, color: string) => (
    <button
      key={val}
      disabled={busy}
      onClick={() => post("/api/autonomy/mode", { mode: val })}
      style={{
        ...segBtn,
        color: mode === val ? "#04060c" : "#9fb4c6",
        background: mode === val ? color : "transparent",
        boxShadow: mode === val ? `0 0 14px ${color}66` : "none",
        fontWeight: mode === val ? 800 : 600,
      }}
    >
      {label}
    </button>
  );

  return (
    <div style={bar}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={lbl}>AUTONOMY</span>
        <div style={segWrap}>
          {seg("OFF", "off", "#6b7a8d")}
          {seg("PROPOSE", "propose", AMBER)}
          {seg("AUTO", "auto", CYAN)}
        </div>
      </div>

      <div style={div} />

      <button
        disabled={busy}
        onClick={() => post("/api/autonomy/mode", { kill_switch: !kill })}
        style={{
          ...pill,
          color: kill ? "#04060c" : RED,
          background: kill ? RED : "rgba(255,93,108,0.10)",
          borderColor: kill ? RED : "rgba(255,93,108,0.4)",
          boxShadow: kill ? `0 0 16px ${RED}66` : "none",
        }}
        title="Halt all autonomous action immediately"
      >
        {kill ? "◼ HALTED — RESUME" : "⏻ KILL SWITCH"}
      </button>

      <div style={div} />

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={lbl}>NEURAL CORE</span>
        <button
          disabled={busy || cortex === null}
          onClick={() => post("/api/cortex/enable", { enabled: !cortex })}
          style={{
            ...pill,
            color: cortex ? "#04060c" : "#9fb4c6",
            background: cortex ? VIOLET : "transparent",
            borderColor: cortex ? VIOLET : "rgba(167,139,250,0.4)",
            boxShadow: cortex ? `0 0 14px ${VIOLET}66` : "none",
          }}
          title="Whether the trained neural core influences live confluence"
        >
          {cortex === null ? "…" : cortex ? "◉ INFLUENCING TRADES" : "○ SHADOW ONLY"}
        </button>
      </div>

      <button onClick={() => setOpen(false)} style={xBtn} title="hide">×</button>
    </div>
  );
}

const bar: React.CSSProperties = {
  position: "fixed", bottom: 18, left: "50%", transform: "translateX(-50%)", zIndex: 60,
  display: "flex", alignItems: "center", gap: 12, padding: "9px 14px",
  fontFamily: "ui-monospace, monospace", fontSize: 11,
  background: "linear-gradient(158deg, rgba(9,15,26,0.92), rgba(7,12,22,0.84))",
  border: "1px solid rgba(96,214,230,0.3)", borderRadius: 12,
  backdropFilter: "blur(18px) saturate(1.2)", WebkitBackdropFilter: "blur(18px) saturate(1.2)",
  boxShadow: "0 18px 60px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.05)",
};
const lbl: React.CSSProperties = { fontSize: 9, letterSpacing: 1.5, color: "#7f93a6", textTransform: "uppercase" };
const segWrap: React.CSSProperties = {
  display: "inline-flex", padding: 2, borderRadius: 9,
  background: "rgba(18,28,42,0.6)", border: "1px solid rgba(120,170,200,0.18)",
};
const segBtn: React.CSSProperties = {
  border: "none", borderRadius: 7, padding: "4px 11px", cursor: "pointer",
  fontSize: 10.5, letterSpacing: 0.6, fontFamily: "ui-monospace, monospace", transition: "all .15s",
};
const pill: React.CSSProperties = {
  border: "1px solid", borderRadius: 9, padding: "6px 12px", cursor: "pointer",
  fontSize: 10.5, fontWeight: 700, letterSpacing: 0.6, fontFamily: "ui-monospace, monospace",
  transition: "all .15s", whiteSpace: "nowrap",
};
const div: React.CSSProperties = { width: 1, height: 22, background: "rgba(120,170,200,0.18)" };
const xBtn: React.CSSProperties = {
  cursor: "pointer", background: "transparent", color: "#5a6b7d", border: "none",
  fontSize: 15, lineHeight: 1, marginLeft: 2,
};
const reopen: React.CSSProperties = {
  position: "fixed", bottom: 18, left: "50%", transform: "translateX(-50%)", zIndex: 60,
  cursor: "pointer", background: "linear-gradient(180deg, rgba(60,240,228,0.16), rgba(167,139,250,0.10))",
  color: "#eafcff", border: "1px solid rgba(96,214,230,0.4)", borderRadius: 10,
  padding: "8px 14px", fontFamily: "ui-monospace, monospace", fontSize: 11, letterSpacing: 1.5,
  backdropFilter: "blur(14px)",
};
