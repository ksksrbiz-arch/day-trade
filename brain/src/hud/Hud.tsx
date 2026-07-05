"use client";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { FIRE_COLORS, FIRE_KINDS, KIND_COLORS } from "../contract";

function ago(ts: number) {
  const s = Math.max(0, (Date.now() - ts) / 1000);
  return s < 60 ? `${s.toFixed(0)}s` : `${(s / 60).toFixed(0)}m`;
}

// arc-reactor style status ring
function Reactor({ online, load }: { online: boolean; load: number }) {
  const c = online ? "var(--cyan)" : "var(--dim)";
  const r = 16, circ = 2 * Math.PI * r;
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" className="reactor">
      <circle cx="20" cy="20" r={r} fill="none" stroke="rgba(54,226,255,.15)" strokeWidth="3" />
      <circle cx="20" cy="20" r={r} fill="none" stroke={c} strokeWidth="3" strokeLinecap="round"
        strokeDasharray={`${circ}`} strokeDashoffset={circ * (1 - Math.min(1, load))}
        transform="rotate(-90 20 20)" />
      <circle cx="20" cy="20" r="6" fill={c} opacity={online ? 0.9 : 0.3}>
        {online && <animate attributeName="opacity" values="0.5;1;0.5" dur="1.8s" repeatCount="indefinite" />}
      </circle>
    </svg>
  );
}

function useFps() {
  const [fps, setFps] = useState(0);
  useEffect(() => {
    let raf = 0, last = performance.now(), frames = 0;
    const loop = () => {
      frames++; const now = performance.now();
      if (now - last >= 1000) { setFps(frames); frames = 0; last = now; }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);
  return fps;
}

export default function Hud() {
  const connected = useStore((s) => s.connected);
  const paused = useStore((s) => s.paused);
  const rate = useStore((s) => s.rate);
  const autoRotate = useStore((s) => s.autoRotate);
  const filters = useStore((s) => s.filters);
  const selected = useStore((s) => s.selected);
  const byId = useStore((s) => s.byId);
  const perNode = useStore((s) => s.perNode);
  const feed = useStore((s) => s.feed);
  const counts = useStore((s) => s.counts);
  const total = useStore((s) => s.total);
  const nodes = useStore((s) => s.nodes);
  const groupHidden = useStore((s) => s.groupHidden);
  const { togglePause, setRate, toggleRotate, toggleFilter, toggleGroup, select } = useStore.getState();
  const fps = useFps();
  const [query, setQuery] = useState("");
  const recent = feed.filter((e) => Date.now() - e.ts < 4000).length;
  const groups = [...new Set(nodes.map((n) => n.group || ""))].filter(Boolean);
  const runSearch = () => {
    const q = query.trim().toLowerCase();
    if (!q) return;
    const hit = nodes.find((n) => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
    if (hit) select(hit.id);
  };

  const node = selected ? byId[selected] : null;
  const nodeFeed = (selected && perNode[selected]) || [];

  return (
    <>
      {/* Stark targeting frame + ambient scan sweep */}
      <div className="stark-frame"><i className="tl" /><i className="tr" /><i className="bl" /><i className="br" /></div>
      <div className="stark-sweep" />

      {/* title bar */}
      <div className="hud panel tc">
        <div className="title"><span style={{ color: "var(--cyan)" }}>◆</span> PLATFORM&nbsp;BRAIN</div>
        <div className="subt">Neural Uplink · Live Telemetry</div>
      </div>

      {/* command / status */}
      <div className="hud panel tl">
        <div className="row">
          <Reactor online={connected} load={Math.min(1, recent / 8)} />
          <div>
            <div className="title" style={{ fontSize: 12 }}>SYSTEM</div>
            <div className="mono" style={{ fontSize: 10, color: connected ? "var(--cyan)" : "var(--dim)" }}>
              {connected ? "● ONLINE" : "○ LINKING"} · {fps} FPS
            </div>
          </div>
        </div>
        <div className="row mono" style={{ marginTop: 10, justifyContent: "space-between", fontSize: 11 }}>
          <span><span className="dim">NODES</span> {nodes.length}</span>
          <span><span className="dim">EVENTS</span> {total}</span>
          <span><span className="dim">FIRING</span> {recent}</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" onClick={togglePause}>{paused ? "▶ Resume" : "⏸ Pause"}</button>
          <button className="btn" onClick={toggleRotate}>{autoRotate ? "◐ Orbit" : "◑ Static"}</button>
        </div>
        <div className="row" style={{ marginTop: 10, gap: 8 }}>
          <span className="dim mono" style={{ fontSize: 10 }}>FLOW</span>
          <input type="range" min={0.05} max={1} step={0.05} value={rate}
            onChange={(e) => setRate(parseFloat(e.target.value))} style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 10, width: 32 }}>{Math.round(rate * 100)}%</span>
        </div>
        <div className="row" style={{ marginTop: 10, gap: 6 }}>
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
            placeholder="search node…" className="mono"
            style={{ flex: 1, background: "rgba(255,255,255,.05)", border: "1px solid rgba(255,255,255,.12)", color: "inherit", borderRadius: 6, padding: "4px 8px", fontSize: 11 }} />
          <button className="btn" onClick={runSearch}>⌕</button>
        </div>
        <div className="hdr" style={{ margin: "10px 0 5px" }}>GROUPS — FILTER</div>
        <div className="row" style={{ flexWrap: "wrap", gap: 5 }}>
          {groups.map((g) => (
            <button key={g} className="btn" style={{ opacity: groupHidden[g] ? 0.35 : 1, fontSize: 10, padding: "3px 7px" }}
              onClick={() => toggleGroup(g)}>{g}</button>
          ))}
        </div>
      </div>

      {/* legend / filters */}
      <div className="hud panel bl">
        <div className="hdr" style={{ marginBottom: 7 }}>SYNAPSE TYPES — FILTER</div>
        {FIRE_KINDS.map((k) => (
          <div key={k} className="legrow" style={{ opacity: filters[k] ? 1 : 0.32 }} onClick={() => toggleFilter(k)}>
            <span className="swatch" style={{ background: FIRE_COLORS[k], boxShadow: `0 0 9px ${FIRE_COLORS[k]}` }} />
            <span style={{ flex: 1 }}>{k}</span>
            <span className="mono dim">{counts[k]}</span>
          </div>
        ))}
      </div>

      {/* node inspector */}
      {node && (
        <div className="hud panel tr">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div className="title" style={{ color: KIND_COLORS[node.kind], fontSize: 14 }}>{node.label}</div>
            <button className="btn" onClick={() => select(null)}>✕</button>
          </div>
          <div className="mono dim" style={{ fontSize: 10, marginTop: 2 }}>
            {node.kind.toUpperCase()} · {node.group}
          </div>
          {node.kind === "connector" && (
            <div className="row mono" style={{ marginTop: 8, gap: 7, fontSize: 11 }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%",
                background: (node.meta as any)?.status === "online" ? "#34d399" : "#ef4444",
                boxShadow: `0 0 8px ${(node.meta as any)?.status === "online" ? "#34d399" : "#ef4444"}` }} />
              <span style={{ color: (node.meta as any)?.status === "online" ? "#34d399" : "#ef4444" }}>
                {String((node.meta as any)?.status || "unknown").toUpperCase()}
              </span>
              <span className="dim">{String((node.meta as any)?.detail || "")}</span>
            </div>
          )}
          <div className="hdr" style={{ margin: "11px 0 2px" }}>NEURAL ACTIVITY</div>
          <div className="feed">
            {nodeFeed.length === 0 && <div className="dim">no recent synapses</div>}
            {nodeFeed.map((e, i) => (
              <div key={i} className="ev">
                <span className="swatch sm" style={{ background: FIRE_COLORS[e.kind], boxShadow: `0 0 7px ${FIRE_COLORS[e.kind]}` }} />
                <span className="evk">{e.kind}</span>
                <span className="muted" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {e.source === node.id ? "▶ " + (byId[e.target]?.label || e.target) : "◀ " + (byId[e.source]?.label || e.source)}
                  {e.summary ? " · " + e.summary : ""}
                </span>
                <span className="mono dim">{ago(e.ts)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* live feed */}
      <div className="hud panel br">
        <div className="hdr">LIVE SYNAPSE FEED</div>
        <div className="feed" style={{ maxHeight: 200 }}>
          {feed.filter((e, i, a) => i === 0 || !(a[i - 1].source === e.source && a[i - 1].target === e.target && a[i - 1].summary === e.summary)).slice(0, 9).map((e, i) => (
            <div key={i} className="ev">
              <span className="swatch sm" style={{ background: FIRE_COLORS[e.kind], boxShadow: `0 0 7px ${FIRE_COLORS[e.kind]}` }} />
              <span className="muted" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {(byId[e.source]?.label || e.source)} <span className="dim">▶</span> {(byId[e.target]?.label || e.target)}
                {e.summary ? " · " + e.summary : ""}
              </span>
            </div>
          ))}
          {feed.length === 0 && <div className="dim">awaiting neural activity…</div>}
        </div>
      </div>
    </>
  );
}
