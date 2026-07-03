"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import Hud from "@/hud/Hud";
import { useLive } from "@/useLive";
import TransformerPanel from "@/TransformerPanel";
import LearningPanel from "@/LearningPanel";
import TransformerNet from "@/TransformerNet";

// r3f must be client-only — disable SSR for the WebGL canvas
const BrainScene = dynamic(() => import("@/scene/BrainScene"), { ssr: false });

export default function BrainPage() {
  useLive(); // DOM-level live connection (outside the Canvas)
  const [netOpen, setNetOpen] = useState(false);
  return (
    <main style={{ position: "fixed", inset: 0 }}>
      <BrainScene />
      <Hud />
      <TransformerPanel />
      <LearningPanel />
      <button onClick={() => setNetOpen(true)}
              style={{ position: "fixed", bottom: 16, right: 16, zIndex: 60, cursor: "pointer",
                       background: "rgba(6,12,16,0.85)", color: "#35e0d8",
                       border: "1px solid rgba(53,224,216,0.3)", borderRadius: 8,
                       padding: "8px 12px", fontFamily: "ui-monospace, monospace", fontSize: 12,
                       letterSpacing: 1 }}>
        ◆ NETWORK VIEW
      </button>
      {netOpen && <TransformerNet onClose={() => setNetOpen(false)} />}
    </main>
  );
}
