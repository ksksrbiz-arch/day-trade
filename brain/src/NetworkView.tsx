"use client";
import { useState } from "react";
import TransformerNet from "@/TransformerNet";
import MeshNet from "@/MeshNet";

// Full-screen network dashboard with a toggle between the live TRANSFORMER
// network and the live AGENT MESH network. Shared chrome (title, toggle, close).
export default function NetworkView({ onClose }: { onClose: () => void }) {
  const [mode, setMode] = useState<"transformer" | "mesh">("transformer");
  const CY = "#35e0d8";
  const tab = (active: boolean): React.CSSProperties => ({
    cursor: "pointer", padding: "6px 14px", borderRadius: 8, fontSize: 12, letterSpacing: 1,
    fontFamily: "ui-monospace,monospace",
    background: active ? "rgba(53,224,216,0.16)" : "rgba(6,12,16,0.6)",
    color: active ? CY : "#8aa",
    border: `1px solid ${active ? "rgba(53,224,216,0.5)" : "rgba(120,150,180,0.25)"}`,
  });
  const legend = mode === "transformer"
    ? "neurons = live latent activation · particles = real attention flow (cyan amplify · red attenuate)"
    : "nodes = mesh layers/agents · particles = live fire-events (purple model · cyan tool · green response · amber read · orange write · red error)";
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 100, background: "#04070a" }}>
      {mode === "transformer" ? <TransformerNet /> : <MeshNet />}
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
      <button onClick={onClose} style={{ position: "fixed", top: 14, right: 16, zIndex: 103, cursor: "pointer",
              background: "rgba(6,12,16,0.85)", color: "#cfe", border: "1px solid rgba(53,224,216,0.3)",
              borderRadius: 8, padding: "6px 12px", fontFamily: "ui-monospace,monospace", fontSize: 12 }}>✕ close</button>
      <div style={{ position: "fixed", bottom: 16, left: 26, right: 120, zIndex: 102,
                    fontFamily: "ui-monospace,monospace", fontSize: 11, color: "#5a7", pointerEvents: "none" }}>
        {legend}
      </div>
    </div>
  );
}
