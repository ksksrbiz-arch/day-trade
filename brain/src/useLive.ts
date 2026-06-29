"use client";
import { useEffect } from "react";
import { useStore } from "./store";
import { LiveEventSource } from "./eventSource/live";
import { computeLayout } from "./layout3d";

// DOM-level live connection (runs outside the WebGL Canvas for reliability).
export function useLive() {
  const setTopology = useStore((s) => s.setTopology);
  const addFire = useStore((s) => s.addFire);
  const setConnected = useStore((s) => s.setConnected);
  useEffect(() => {
    const src = new LiveEventSource();
    let unsub = () => {};
    let alive = true;
    src
      .getTopology()
      .then(({ nodes, edges }) => {
        if (!alive) return;
        let layout: Record<string, [number, number, number]>;
        try {
          layout = computeLayout(nodes, edges);
        } catch (e) {
          console.error("layout failed, using radial fallback", e);
          layout = {};
          nodes.forEach((n, i) => {
            const a = i * 0.6, r = 16 + (i % 5) * 3;
            layout[n.id] = [Math.cos(a) * r, (i % 9) * 3 - 12, Math.sin(a) * r];
          });
        }
        setTopology(nodes, edges, layout);
        setConnected(true);
        unsub = src.subscribe(addFire);
      })
      .catch((e) => console.error("topology load failed", e));
    return () => {
      alive = false;
      unsub();
    };
  }, [setTopology, addFire, setConnected]);
}
