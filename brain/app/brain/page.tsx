"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import Hud from "@/hud/Hud";
import { useLive } from "@/useLive";
import TransformerPanel from "@/TransformerPanel";
import LearningPanel from "@/LearningPanel";
import NetworkView from "@/NetworkView";
import BrainControls from "@/BrainControls";
import PsychePanel from "@/PsychePanel";
import DreamPanel from "@/DreamPanel";

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
      <BrainControls />
      <PsychePanel />
      <DreamPanel />
      <button
        onClick={() => setNetOpen(true)}
        style={{
          position: "fixed", bottom: 18, right: 18, zIndex: 60, cursor: "pointer",
          background: "linear-gradient(180deg, rgba(60,240,228,0.16), rgba(167,139,250,0.10))",
          color: "#eafcff", border: "1px solid rgba(96,214,230,0.4)", borderRadius: 10,
          padding: "10px 16px", fontFamily: "ui-monospace, monospace", fontSize: 12,
          fontWeight: 600, letterSpacing: 1.5, textTransform: "uppercase",
          backdropFilter: "blur(14px) saturate(1.2)",
          boxShadow: "0 10px 34px rgba(0,0,0,0.5), 0 0 22px rgba(60,240,228,0.28), inset 0 1px 0 rgba(255,255,255,0.08)",
        }}
      >
        ◆ Network View
      </button>
      {netOpen && <NetworkView onClose={() => setNetOpen(false)} />}
    </main>
  );
}
