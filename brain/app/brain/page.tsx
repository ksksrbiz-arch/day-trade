"use client";
import dynamic from "next/dynamic";
import Hud from "@/hud/Hud";
import { useLive } from "@/useLive";
import TransformerPanel from "@/TransformerPanel";
import LearningPanel from "@/LearningPanel";

// r3f must be client-only — disable SSR for the WebGL canvas
const BrainScene = dynamic(() => import("@/scene/BrainScene"), { ssr: false });

export default function BrainPage() {
  useLive(); // DOM-level live connection (outside the Canvas)
  return (
    <main style={{ position: "fixed", inset: 0 }}>
      <BrainScene />
      <Hud />
      <TransformerPanel />
      <LearningPanel />
    </main>
  );
}
