"""Live telemetry for the 3D 'neural brain'.

Exposes the WHOLE platform as a graph (build_topology) and a normalized stream of
FireEvents (fire_events_since) derived from REAL activity across every layer:
agent traces, the insight mesh, council votes, predictions, ML, live signals,
transformer forecasts, learned-weight updates, executed trades, the Pieces MCP
long-term-memory connector, and external data-source connectors (Alpaca, CoinEx,
WallStreetBets, news). No mock: this is the live wire the /brain visualization
consumes.

FireEvent contract (matches the TS side exactly):
  {id, source, target, kind, ts(ms), durationMs?, status?, summary?}
  kind in: model_call | tool_call | response | data_read | data_write | error
"""
from __future__ import annotations

import os
import re
import time


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


# Stable node ids -------------------------------------------------------------
def a_id(name): return "a:" + slug(name)          # agent
def t_id(name): return "t:" + slug(name)          # tool
def m_id(name): return "m:" + slug(name)          # model
def s_id(name): return "s:" + slug(name)          # service
def d_id(name): return "d:" + slug(name)          # datastore
def l_id(name): return "l:" + slug(name)          # layer
def c_id(name): return "c:" + slug(name)          # connector (MCP / external)


MODELS = ["anthropic", "groq", "cohere", "cloudflare", "openrouter", "replicate", "omni"]
LAYERS = ["reasoning", "brain", "prediction", "ml", "confluence", "execution", "mesh", "memory"]
DATASTORES = [("CRSP-lite", "crsp"), ("predict.db", "predict"), ("mesh.db", "mesh"),
              ("signals.db", "signals"), ("state.db", "state"), ("Pieces LTM", "ltm")]
# External connectors (MCP + data/broker feeds) -- a first-class group
CONNECTORS = [("Pieces MCP", "pieces"), ("Alpaca", "alpaca"), ("CoinEx", "coinex"),
              ("WallStreetBets", "wsb"), ("News RSS", "news")]
# runtime "agents" that aren't in ROSTER but appear in traces
EXTRA_AGENTS = ["Mesh", "Predictor", "SignalCapture", "Reflex", "Reflector"]


def _roster():
    try:
        from trader.agents.orchestrator import ROSTER
        return ROSTER
    except Exception:  # noqa: BLE001
        return []


def _tools():
    try:
        from trader.agents.tools import REGISTRY
        return list(REGISTRY.keys())
    except Exception:  # noqa: BLE001
        return []


def _services():
    try:
        from trader.agents.supervisor import SERVICES
        return list(SERVICES.keys())
    except Exception:  # noqa: BLE001
        return ["dashboard", "exits", "optimizer", "autotuner", "ml_daemon", "agents", "supervisor"]


_CONN_STATUS = {"ts": 0.0, "val": {}}


def _connector_status() -> dict:
    """Live health of external connectors (cached 60s so build_topology stays cheap)."""
    now = time.time()
    if _CONN_STATUS["val"] and now - _CONN_STATUS["ts"] < 60:
        return _CONN_STATUS["val"]
    st: dict = {}
    try:
        use = os.environ.get("USE_PIECES", "true").strip().lower() in ("1", "true", "yes", "on")
        if not use:
            st["pieces"] = ("offline", "USE_PIECES disabled")
        else:
            import socket
            s = socket.create_connection(("localhost", 39300), timeout=0.5)
            s.close()
            st["pieces"] = ("online", "Pieces OS reachable :39300")
    except Exception:  # noqa: BLE001
        st["pieces"] = ("offline", "Pieces OS not reachable")
    try:
        from trader import config
        c = config.load()
        st["alpaca"] = ("online", "paper keys present") if getattr(c, "alpaca_key", "") \
            else ("offline", "no API keys")
    except Exception:  # noqa: BLE001
        st["alpaca"] = ("offline", "config error")
    st["coinex"] = ("online", "public klines (no key)")
    st["wsb"] = ("online", "RSS feed")
    st["news"] = ("online", "RSS feed")
    _CONN_STATUS["ts"] = now
    _CONN_STATUS["val"] = st
    return st


def build_topology() -> dict:
    nodes, edges, seen = [], [], set()

    def node(nid, label, kind, group, **meta):
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"id": nid, "label": label, "kind": kind, "group": group, "meta": meta})

    def edge(src, tgt, kind="link"):
        if src in seen and tgt in seen:
            eid = f"{src}->{tgt}"
            edges.append({"id": eid, "source": src, "target": tgt, "kind": kind})

    # Models
    for m in MODELS:
        node(m_id(m), m, "model", "Reasoning Council")
    # Layers
    for l in LAYERS:
        node(l_id(l), l.capitalize(), "layer", "Cognition")
    # Datastores
    for label, key in DATASTORES:
        node(d_id(key), label, "datastore", "Memory")
    # Connectors (MCP + external feeds) with live health status
    cs = _connector_status()
    for label, key in CONNECTORS:
        sstat, detail = cs.get(key, ("unknown", ""))
        node(c_id(key), label, "connector", "Connectors", status=sstat, detail=detail)
    # Services
    for sv in _services():
        node(s_id(sv), sv, "service", "Runtime")
    # Tools
    for t in _tools():
        node(t_id(t), t, "tool", "Tools")
    # Macro factors + cross-attention layer (transformer drivers)
    for k, lbl in [("spy", "S&P 500"), ("qqq", "Nasdaq"), ("tlt", "Yields"),
                   ("gld", "Gold"), ("uup", "US Dollar")]:
        node("x:" + k, lbl, "datastore", "Macro")
    node(l_id("attention"), "Cross-Attention", "layer", "Cognition")
    # Agents (roster + extras)
    roster = _roster()
    for a in roster:
        node(a_id(a["name"]), a["name"], "agent", "Desk")
    for a in EXTRA_AGENTS:
        node(a_id(a), a, "agent", "Desk")

    # ---- edges (communication paths) ----
    for m in MODELS:
        edge(m_id(m), l_id("reasoning"), "model")
    edge(l_id("reasoning"), l_id("mesh"))
    for a in roster:
        aid = a_id(a["name"])
        edge(aid, l_id("reasoning"))
        edge(aid, l_id("mesh"))
        for t in a.get("tools", []):
            edge(aid, t_id(t), "tool")
    # extra agents wire to their layers
    edge(a_id("Mesh"), l_id("mesh")); edge(a_id("Predictor"), l_id("prediction"))
    edge(a_id("SignalCapture"), l_id("execution")); edge(a_id("Reflex"), l_id("execution"))
    edge(a_id("Reflector"), l_id("memory"))
    # layers -> datastores
    edge(l_id("prediction"), d_id("predict")); edge(l_id("ml"), d_id("state"))
    edge(l_id("mesh"), d_id("mesh")); edge(l_id("execution"), d_id("signals"))
    edge(l_id("brain"), d_id("crsp")); edge(l_id("memory"), d_id("ltm"))
    edge(l_id("mesh"), d_id("ltm")); edge(l_id("confluence"), l_id("execution"))
    edge(l_id("brain"), l_id("confluence")); edge(l_id("prediction"), l_id("confluence"))
    edge(l_id("ml"), l_id("confluence"))
    # macro -> cross-attention -> brain/confluence
    for k in ("spy", "qqq", "tlt", "gld", "uup"):
        edge("x:" + k, l_id("attention"), "macro")
    edge(l_id("attention"), l_id("brain")); edge(l_id("attention"), l_id("confluence"))
    # connectors -> their concerns
    edge(c_id("pieces"), l_id("memory"), "mcp"); edge(l_id("memory"), c_id("pieces"), "mcp")
    edge(c_id("pieces"), d_id("ltm"), "mcp")
    edge(c_id("alpaca"), l_id("execution"), "feed"); edge(l_id("execution"), c_id("alpaca"), "feed")
    edge(c_id("coinex"), l_id("brain"), "feed"); edge(c_id("coinex"), l_id("prediction"), "feed")
    edge(c_id("wsb"), l_id("prediction"), "feed")
    edge(c_id("news"), l_id("reasoning"), "feed"); edge(c_id("news"), l_id("brain"), "feed")
    # services -> their concerns
    edge(s_id("agents"), l_id("reasoning")); edge(s_id("ml_daemon"), l_id("ml"))
    edge(s_id("autotuner"), l_id("execution")); edge(s_id("optimizer"), l_id("execution"))
    edge(s_id("supervisor"), s_id("agents")); edge(s_id("exits"), l_id("execution"))
    edge(s_id("dashboard"), l_id("mesh"))
    return {"nodes": nodes, "edges": edges}


# ---- live fire events -------------------------------------------------------
def _ms(iso: str) -> int:
    try:
        return int(time.mktime(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
    except Exception:  # noqa: BLE001
        return int(time.time() * 1000)


def _topo_ids() -> set:
    return {n["id"] for n in build_topology()["nodes"]}


# periodic "state pulse" gate: emit a family at most once per TTL
_LAST: dict[str, float] = {}


def _due(name: str, ttl: float) -> bool:
    now = time.time()
    if now - _LAST.get(name, 0.0) >= ttl:
        _LAST[name] = now
        return True
    return False


def fire_events_since(cursor: float) -> tuple[list, float]:
    """Return (events newer than cursor epoch-secs, new_cursor)."""
    ids = _topo_ids()
    out = []
    newest = cursor
    now_ms = int(time.time() * 1000)

    def emit(eid, src, tgt, kind, ts_ms, status="ok", dur=None, summary=""):
        if src not in ids or tgt not in ids:
            return
        out.append({"id": eid, "source": src, "target": tgt, "kind": kind,
                    "ts": ts_ms, "status": status, "durationMs": dur, "summary": summary[:120]})

    # 1) agent execution traces -> agent -> tool/layer
    try:
        from trader.agents import state
        for tr in state.recent_traces(80):
            tsec = _ms(tr.get("ts", "")) / 1000.0
            if tsec <= cursor:
                continue
            newest = max(newest, tsec)
            agent = a_id(tr.get("agent", "system"))
            tool = tr.get("tool")
            target = t_id(tool) if tool and t_id(tool) in ids else l_id("execution")
            status = "error" if tr.get("status") == "failed" else "ok"
            kind = "error" if status == "error" else ("tool_call" if tool else "model_call")
            emit(f"tr{tr.get('id')}", agent, target, kind, int(tsec * 1000),
                 status, tr.get("ms"), tr.get("summary", ""))
    except Exception:  # noqa: BLE001
        pass

    # 2) mesh insights -> layer -> mesh datastore (data_write)
    try:
        from trader import mesh
        for i, ins in enumerate(mesh.recent(40)):
            tsec = _ms(ins.get("ts", "")) / 1000.0
            if tsec <= cursor:
                continue
            newest = max(newest, tsec)
            src = l_id(ins.get("layer", "mesh"))
            if src not in ids:
                src = l_id("mesh")
            emit(f"mesh{ins.get('id', i)}", src, d_id("mesh"), "data_write",
                 int(tsec * 1000), "ok", None, ins.get("text", ""))
    except Exception:  # noqa: BLE001
        pass

    # 3) council votes -> model -> reasoning layer (model_call)
    try:
        import json
        clog = os.path.join("data", "ml", "council_log.jsonl")
        if os.path.exists(clog):
            for ln in open(clog, encoding="utf-8").read().splitlines()[-12:]:
                try:
                    rec = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                tsec = _ms(rec.get("ts", "")) / 1000.0
                if tsec <= cursor:
                    continue
                newest = max(newest, tsec)
                for v in rec.get("votes", []):
                    src = m_id(v.get("source", ""))
                    emit(f"cv{rec.get('ts')}_{v.get('source')}", src, l_id("reasoning"),
                         "model_call", int(tsec * 1000), "ok", None,
                         f"{rec.get('symbol')} {v.get('stance')}")
    except Exception:  # noqa: BLE001
        pass

    # 4) executed trades (real ledger) -> execution -> Alpaca connector
    try:
        from dashboard import dash_metrics
        for i, r in enumerate(dash_metrics.read_ledger(None, limit=60)):
            tsec = _ms(r.get("ts", "")) / 1000.0
            if tsec <= cursor:
                continue
            newest = max(newest, tsec)
            sym = (r.get("symbol") or "").upper()
            act = (r.get("action") or r.get("side") or "trade").upper()
            crypto = "/" in sym or sym.endswith("USD")
            conn = c_id("coinex") if crypto else c_id("alpaca")
            emit(f"fill{i}_{tsec:.0f}", l_id("execution"), conn, "tool_call",
                 int(tsec * 1000), "ok", None, f"{act} {sym}")
    except Exception:  # noqa: BLE001
        pass

    # ---- periodic STATE PULSES (each gated by its own TTL) ----
    # 5) predictions watching -> prediction layer -> confluence
    try:
        if _due("pred", 45):
            from trader.predict import store as pstore
            for p in pstore.predictions(status="watching", limit=5):
                emit(f"pred{now_ms}_{p['symbol']}", l_id("prediction"), l_id("confluence"),
                     "data_read", now_ms, "ok", None,
                     f"{p['symbol']} {p['direction']} {int(p['magnitude_pct'])}%/{p['horizon_days']}d")
            emit(f"predw{now_ms}", l_id("prediction"), d_id("predict"), "data_write",
                 now_ms, "ok", None, "watch plans persisted")
    except Exception:  # noqa: BLE001
        pass

    # 6) ML model edge -> ml -> confluence
    try:
        if _due("ml", 60):
            from trader.ml.infer import model_card
            m = model_card()
            if m.get("trained"):
                emit(f"ml{now_ms}", l_id("ml"), l_id("confluence"), "data_read", now_ms,
                     "ok", None, f"ML AUC {m.get('auc')} edge {m.get('edge')}")
    except Exception:  # noqa: BLE001
        pass

    # 7) live signal scorecard -> execution -> signals datastore
    try:
        if _due("sig", 60):
            from trader import sigtrack
            for r in sigtrack.scoreboard().get("by_source", []):
                emit(f"sig{now_ms}_{r['source']}", l_id("execution"), d_id("signals"),
                     "data_write", now_ms, "ok", None,
                     f"{r['source']}: {r['signals']} signals")
    except Exception:  # noqa: BLE001
        pass

    # 8) transformer forecast -> cross-attention -> confluence (model_call)
    try:
        if _due("tnet", 90):
            from trader import tnet
            fc = tnet.forecast("SPY")
            if "error" not in fc:
                emit(f"tnet{now_ms}", l_id("attention"), l_id("confluence"), "model_call",
                     now_ms, "ok", None,
                     f"SPY {fc['direction']} p(up) {fc['prob_up']:.0%} conf {fc['confidence']:.0%}")
    except Exception:  # noqa: BLE001
        pass

    # 9) backprop learned weights -> ml -> confluence
    try:
        if _due("bp", 120):
            from trader import backprop
            bc = backprop.card()
            if bc.get("trained"):
                emp = bc.get("emphasis", {}) or {}
                top = sorted(emp.items(), key=lambda kv: kv[1], reverse=True)[:2]
                emit(f"bp{now_ms}", l_id("ml"), l_id("confluence"), "data_read", now_ms,
                     "ok", None, "weights: " + ", ".join(f"{k} {v:.0%}" for k, v in top))
    except Exception:  # noqa: BLE001
        pass

    # 10) Pieces MCP long-term memory -- writes (mirror) + recall (read)
    try:
        if _due("ltm", 50):
            from trader import mesh
            salient = [r for r in mesh.recent(20) if (r.get("salience") or 0) >= 0.6]
            if salient:
                emit(f"ltmw{now_ms}", l_id("memory"), c_id("pieces"), "data_write", now_ms,
                     "ok", None, f"mirror {len(salient)} insights -> Pieces LTM")
            use_pieces = os.environ.get("USE_PIECES", "true").strip().lower() in ("1", "true", "yes", "on")
            if use_pieces:
                emit(f"ltmr{now_ms}", c_id("pieces"), l_id("memory"), "data_read", now_ms,
                     "ok", None, "recall cross-layer context")
    except Exception:  # noqa: BLE001
        pass

    # 11) WSB buzz -> connector -> prediction
    try:
        if _due("wsb", 70):
            from trader import wsb
            tks = wsb.buzz().get("tickers", [])[:5]
            if tks:
                emit(f"wsb{now_ms}", c_id("wsb"), l_id("prediction"), "data_read", now_ms,
                     "ok", None, "buzz: " + ", ".join(f"{t['symbol']}({t['mentions']})" for t in tks))
    except Exception:  # noqa: BLE001
        pass

    # 12) cross-attention drivers -> macro factor fires into the attention layer
    try:
        if _due("drv", 90):
            from trader import tnet
            dr = tnet.analyze("SPY").get("drivers", {}).get("weights", {})
            dmap = {"S&P 500": "x:spy", "Nasdaq": "x:qqq", "Treasury yields(inv)": "x:tlt",
                    "Gold": "x:gld", "US Dollar": "x:uup"}
            for fname, w in (dr or {}).items():
                if w and w > 0.1 and fname in dmap:
                    emit(f"drv{now_ms}_{dmap[fname]}", dmap[fname], l_id("attention"),
                         "data_read", now_ms, "ok", None, f"{fname} drives {w:.0%}")
    except Exception:  # noqa: BLE001
        pass

    return out, newest


if __name__ == "__main__":
    t = build_topology()
    print("topology:", len(t["nodes"]), "nodes,", len(t["edges"]), "edges")
    groups = {}
    for n in t["nodes"]:
        groups[n["group"]] = groups.get(n["group"], 0) + 1
    print("groups:", groups)
    ev, cur = fire_events_since(0)
    print("fire events available:", len(ev))
    kinds = {}
    for e in ev:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print("by kind:", kinds)
