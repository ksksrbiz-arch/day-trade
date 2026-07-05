"use client";
import { useState } from "react";
import TransformerNet from "@/TransformerNet";
import MeshNet from "@/MeshNet";

// Full-screen network dashboard: toggle between the live TRANSFORMER and the
// live AGENT MESH, with a symbol picker (transformer) and kind filters (mesh).
const SYMBOLS = ["AUTO", "SPY", "QQQ", "NVDA", "TSLA", "AAPL", "MSFT"];
const KINDS: [string, string][] = [
  ["model_call", "#a855f7"], ["tool_call", "#35e0d8"], ["response", "#34d399"],
  ["data_read", "#f2a53a"], ["data_write", "#fb923c"], ["error", "#ff5a6a"],
];
const CY = "#35e0d8";

export default function NetworkView({ onClose }: { onClose: () => void }) {
  const [mode, setMode] = useState<"transformer" | "mesh">("transformer");
  const [sym, setSym] = useState("AUTO");
  const [kinds, setKinds] = useState<Set<string>>(new Set(KINDS.map((k) => k[0])));

  const tab = (active: boolean): React.CSSProperties => ({
    cursor: "pointer", padding: "7px 16px", borderRadius: 10, fontSize: 12, letterSpacing: 1.2,
    fontFamily: "ui-monospace,monospace", fontWeight: 600, textTransform: "uppercase",
    background: active
      ? "linear-gradient(180deg, rgba(60,240,228,0.22), rgba(60,240,228,0.06))"
      : "rgba(10,18,30,0.55)",
    color: active ? CY : "#8aa", backdropFilter: "blur(12px)",
    border: `1px solid ${active ? "rgba(60,240,228,0.55)" : "rgba(120,150,180,0.22)"}`,
    boxShadow: active ? "0 0 18px rgba(60,240,228,0.3)" : "none",
  });
  const toggleKind = (k: string) => setKinds((prev) => {
    const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n;
  });

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 100,
                  background: "radial-gradient(120% 90% at 12% -10%, rgba(60,240,228,0.10), transparent 55%),"
                    + " radial-gradient(110% 90% at 100% 8%, rgba(167,139,250,0.10), transparent 52%),"
                    + " linear-gradient(180deg,#050a14 0%,#04070e 60%,#03050b 100%)" }}>
      {mode === "transformer" ? <TransformerNet symbol={sym} /> : <MeshNet kinds={kinds} />}

      <div style={{ position: "fixed", top: 16, left: 26, right: 26, zIndex: 102, display: "flex",
                    justifyContent: "space-between", alignItems: "center",
                    fontFamily: "ui-monospace,monospace", pointerEvents: "none" }}>
        <div style={{ fontWeight: 700, letterSpacing: 2, color: CY, fontSize: 14,
                      textShadow: "0 0 18px rgba(53,224,216,0.5)" }}>
          ◆ {mode === "transformer" ? "TRANSFORMER" : "AGENT MESH"} NETWORK · LIVE
        </div>
        <div style={{ display: "flex", gap: 8, pointerEvents: "auto" }}>
          <button style={tab(mode === "transformer")} onClick={() => setMode("transformer")}>TRANSFORMER</button>
          <button style={tab(mode === "mesh")} onClick={() => setMode("mesh")}>AGENT MESH</button>
        </div>
      </div>

      {/* per-mode controls */}
      <div style={{ position: "fixed", top: 52, left: 26, zIndex: 102, display: "flex", gap: 8,
                    alignItems: "center", fontFamily: "ui-monospace,monospace", pointerEvents: "auto" }}>
        {mode === "transformer" ? (
          <>
            <span style={{ fontSize: 11, color: "#8aa" }}>SYMBOL</span>
            <select value={sym} onChange={(e) => setSym(e.target.value)}
                    style={{ background: "rgba(6,12,16,0.85)", color: CY, fontSize: 12,
                             border: "1px solid rgba(53,224,216,0.4)", borderRadius: 6, padding: "4px 8px",
                             fontFamily: "ui-monospace,monospace", cursor: "pointer" }}>
              {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </>
        ) : (
          <>
            <span style={{ fontSize: 11, color: "#8aa", marginRight: 2 }}>FILTER</span>
            {KINDS.map(([k, c]) => {
              const on = kinds.has(k);
              return (
                <button key={k} onClick={() => toggleKind(k)}
                        style={{ cursor: "pointer", fontSize: 10, letterSpacing: 0.5, borderRadius: 6,
                                 padding: "3px 8px", fontFamily: "ui-monospace,monospace",
                                 background: on ? `${c}22` : "rgba(6,12,16,0.6)",
                                 color: on ? c : "#667",
                                 border: `1px solid ${on ? c : "rgba(120,150,180,0.2)"}` }}>
                  {k}
                </button>
              );
            })}
          </>
        )}
      </div>

      <button onClick={onClose} style={{ position: "fixed", top: 16, right: 18, zIndex: 103, cursor: "pointer",
              background: "linear-gradient(180deg, rgba(255,93,108,0.14), rgba(255,93,108,0.05))",
              color: "#ffd7db", border: "1px solid rgba(255,93,108,0.35)", borderRadius: 9,
              padding: "7px 13px", fontFamily: "ui-monospace,monospace", fontSize: 12, letterSpacing: 0.5,
              backdropFilter: "blur(12px)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>✕ CLOSE</button>

      <div style={{ position: "fixed", bottom: 16, left: 26, right: 120, zIndex: 102,
                    fontFamily: "ui-monospace,monospace", fontSize: 11, color: "#5a7", pointerEvents: "none" }}>
        {mode === "transformer"
          ? "neurons = live latent activation · particles = real attention flow (cyan amplify · red attenuate)"
          : "nodes = mesh layers/agents · particles = live fire-events · use FILTER to isolate event kinds"}
      </div>
    </div>
  );
}
