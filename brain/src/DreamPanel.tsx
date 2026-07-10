"use client";
import { useEffect, useState } from "react";

// What the system does while the market sleeps. Shows whether it is AWAKE
// (trading hours) or DREAMING (market closed -> consolidation cycle running),
// the latest dream journal, and the recent overnight history. Honest: the
// "dream" is memory replay + counterfactual replay on real history + belief
// consolidation + curiosity study + offline retraining -- not fantasy.

const BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_TELEMETRY_BASE) ||
  "https://day-trade-backend.onrender.com";

const VIOLET = "#a78bfa";
const CYAN = "#3cf0e4";
const AMBER = "#ffbf4d";
const DIM = "#7f93a6";

type Dream = {
  session?: string; open?: boolean;
  last?: { journal?: string; ts?: string; elapsed_s?: number; phases?: any };
  journal?: { ts?: string; journal?: string }[];
  error?: string;
};

export default function DreamPanel() {
  const [d, setD] = useState<Dream | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${BASE}/api/dream`, { cache: "no-store" }).then((x) => x.json());
        if (alive) setD(r);
      } catch {
        /* transient */
      }
    };
    pull();
    const t = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const dreaming = d ? !d.open : false;
  const label = dreaming ? "DREAMING" : "AWAKE";
  const col = dreaming ? VIOLET : CYAN;

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={{ ...btn, color: col, borderColor: col + "66" }}>
        ☾ DREAM · {label}
      </button>
    );
  }

  const last = d?.last || {};
  const phases = last.phases || {};
  const dr = phases.dream || {};
  const study = phases.study || {};
  const cons = phases.consolidate || {};

  return (
    <div style={panel}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, letterSpacing: 1, color: VIOLET, fontSize: 12 }}>☾ DREAM STATE</div>
        <button onClick={() => setOpen(false)} style={x}>×</button>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 9, color: DIM, letterSpacing: 1.5 }}>MARKET</span>
        <span style={{ fontSize: 16, fontWeight: 800, color: col, textShadow: `0 0 16px ${col}55` }}>{label}</span>
        <span style={{ fontSize: 10, color: DIM }}>· {d?.session || "…"}</span>
      </div>

      {d?.error && <div style={{ fontSize: 10, color: "#ff5d6c" }}>offline: {d.error}</div>}

      <div style={sec}>LAST DREAM JOURNAL</div>
      <div style={{ fontSize: 11.5, color: "#dfeaf2", lineHeight: 1.4, fontStyle: "italic", marginBottom: 8, paddingLeft: 9, borderLeft: `2px solid ${VIOLET}66` }}>
        {last.journal || "no dream recorded yet — the system dreams when the market closes."}
        {last.ts && <div style={{ fontStyle: "normal", color: DIM, fontSize: 9, marginTop: 3 }}>{last.ts} · {last.elapsed_s ?? 0}s</div>}
      </div>

      {(dr.insights && dr.insights.length > 0) && (
        <>
          <div style={sec}>LEARNED IN COUNTERFACTUAL REPLAY</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
            {dr.insights.slice(0, 4).map((s: string, i: number) => (
              <div key={i} style={{ fontSize: 10, color: "#cfe0ea", lineHeight: 1.3 }}>· {s}</div>
            ))}
          </div>
        </>
      )}

      <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
        {typeof dr.scanned_windows === "number" && (
          <span style={chip(CYAN)}>{dr.scanned_windows} windows replayed</span>
        )}
        {typeof cons?.forget?.dropped === "number" && (
          <span style={chip(AMBER)}>{cons.forget.dropped} beliefs forgotten</span>
        )}
        {typeof study?.filed_to_ltm === "number" && study.filed_to_ltm > 0 && (
          <span style={chip(VIOLET)}>{study.filed_to_ltm} topics studied</span>
        )}
      </div>

      {(d?.journal && d.journal.length > 1) && (
        <>
          <div style={sec}>RECENT NIGHTS</div>
          <div style={{ maxHeight: 130, overflow: "auto", display: "flex", flexDirection: "column", gap: 5 }}>
            {d.journal.slice().reverse().slice(0, 8).map((j, i) => (
              <div key={i} style={{ fontSize: 10, color: "#aebfce", lineHeight: 1.3 }}>
                <span style={{ color: DIM, fontSize: 9 }}>{(j.ts || "").slice(5, 16)} </span>
                {(j.journal || "").slice(0, 120)}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

const panel: React.CSSProperties = {
  position: "fixed", left: 18, top: 90, width: 312, padding: 15, zIndex: 50,
  maxHeight: "calc(100vh - 120px)", overflowY: "auto",
  background: "linear-gradient(158deg, rgba(12,10,26,0.94), rgba(8,7,20,0.86))",
  border: "1px solid rgba(167,139,250,0.32)", borderRadius: 12,
  backdropFilter: "blur(18px) saturate(1.2)", WebkitBackdropFilter: "blur(18px) saturate(1.2)",
  color: "#e9f2f8", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  boxShadow: "0 18px 60px rgba(0,0,0,0.55), 0 0 24px rgba(167,139,250,0.12), inset 0 1px 0 rgba(255,255,255,0.05)",
};
const btn: React.CSSProperties = {
  position: "fixed", left: 18, top: 90, zIndex: 50, cursor: "pointer",
  background: "linear-gradient(180deg, rgba(167,139,250,0.14), rgba(167,139,250,0.05))",
  border: "1px solid", borderRadius: 9, padding: "7px 12px",
  fontSize: 11, letterSpacing: 1, fontFamily: "ui-monospace, monospace",
  backdropFilter: "blur(12px)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
};
const x: React.CSSProperties = { cursor: "pointer", background: "transparent", color: "#889", border: "none", fontSize: 16, lineHeight: 1 };
const sec: React.CSSProperties = { fontSize: 9, letterSpacing: 2, textTransform: "uppercase", color: "#8b7fc6", marginBottom: 5 };
const chip = (c: string): React.CSSProperties => ({
  fontSize: 9.5, padding: "2px 7px", borderRadius: 6, background: c + "18",
  border: `1px solid ${c}44`, color: c,
});
