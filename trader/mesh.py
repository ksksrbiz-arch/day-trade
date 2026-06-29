"""Insight mesh -- the shared nervous system.

A single bus every layer publishes to and reads from, so the prediction engine,
the Brain, the reasoning council, the agents, and your Pieces long-term memory
all talk to each other:

  publish(layer, kind, text, ...)   -> writes an insight (idempotent) + mirrors
                                       it into Pieces LTM (idempotent).
  briefing(...)                     -> a compact cross-layer situational summary,
                                       injected into council + agent prompts so
                                       every reasoner sees what the others know.
  recall(query)                     -> asks Pieces LTM for relevant past context.
  snapshot()                        -> pulls the live state of every layer (brain
                                       regime/posture, top predictions + decision
                                       matrix, ML edge) and publishes it, then
                                       stores a combined 'situation' memo in LTM.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "mesh"))
DB = os.environ.get("MESH_DB", os.path.join(_DATA, "mesh.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY, ts TEXT, day TEXT, layer TEXT, kind TEXT,
    symbol TEXT, salience REAL, text TEXT
);
CREATE INDEX IF NOT EXISTS ix_mesh_ts ON insights(ts);
"""

_ltm = None


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def conn():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _ltm_client():
    global _ltm
    if _ltm is None:
        try:
            from .pieces_ltm import PiecesLTM
            url = os.environ.get("PIECES_MCP_URL",
                                 "http://localhost:39300/model_context_protocol/2025-03-26/mcp")
            enabled = os.environ.get("USE_PIECES", "true").strip().lower() in ("1", "true", "yes", "on")
            _ltm = PiecesLTM(url=url, enabled=enabled)
        except Exception:  # noqa: BLE001
            _ltm = False
    return _ltm or None


def publish(layer: str, kind: str, text: str, symbol: str = "",
            salience: float = 0.5, to_ltm: bool = True) -> bool:
    """Idempotently record an insight (per layer/kind/symbol/text/day) and mirror
    high-salience ones into long-term memory."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    h = hashlib.sha1(f"{layer}|{kind}|{symbol}|{text[:80]}|{day}".encode()).hexdigest()[:16]
    c = conn()
    cur = c.execute("INSERT OR IGNORE INTO insights(id,ts,day,layer,kind,symbol,salience,text)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (h, _now(), day, layer, kind, symbol or "", float(salience), text[:500]))
    created = cur.rowcount > 0
    c.commit(); c.close()
    if created and to_ltm and salience >= 0.6:
        cl = _ltm_client()
        if cl:
            try:
                cl.remember(f"[{layer}/{kind}] {symbol}".strip(), text[:1500],
                            dedup_key=h)
            except Exception:  # noqa: BLE001
                pass
    return created


def recent(n: int = 30, layers=None, symbol: str = ""):
    c = conn()
    q = "SELECT * FROM insights"
    cond, args = [], []
    if layers:
        cond.append("layer IN (%s)" % ",".join("?" * len(layers))); args += list(layers)
    if symbol:
        cond.append("symbol=?"); args.append(symbol.upper())
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY ts DESC LIMIT ?"; args.append(n)
    c2 = conn().execute(q, args).fetchall(); c.close()
    return [dict(r) for r in c2]


def graph(window: int = 300) -> dict:
    """Analytics view of the mesh as a graph: layer nodes (volume + salience +
    recency), edges between layers that co-mentioned the same symbol, plus
    distribution + decay metrics. Drives the dashboard mesh visualization."""
    rows = recent(window)
    if not rows:
        return {"nodes": [], "edges": [], "metrics": {"total": 0}}
    now = time.time()

    def _epoch(iso):
        try:
            return time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:  # noqa: BLE001
            return now

    nodes: dict[str, dict] = {}
    sym_layers: dict[str, set] = {}
    sal_hist = {"low": 0, "med": 0, "high": 0}
    per_hour: dict[int, int] = {}
    sym_counts: dict[str, int] = {}
    # GNN-flavored engagement: per-(symbol,layer) attention mass = salience x
    # recency, accumulated. This is the signal both the edge attention and the
    # 1-hop node embedding are built from.
    sl_score: dict[tuple, float] = {}
    self_score: dict[str, float] = {}
    buckets: dict[int, dict] = {}        # time-bucket -> {layer: mass} for co-activation
    for r in rows:
        lay = r["layer"]; sal = float(r.get("salience") or 0.0)
        n = nodes.setdefault(lay, {"layer": lay, "count": 0, "sal_sum": 0.0, "last": ""})
        n["count"] += 1; n["sal_sum"] += sal
        if not n["last"] or r["ts"] > n["last"]:
            n["last"] = r["ts"]
        sal_hist["high" if sal >= 0.7 else "med" if sal >= 0.4 else "low"] += 1
        age_h = (now - _epoch(r["ts"])) / 3600.0
        recency = 1.0 / (1.0 + max(0.0, age_h) / 6.0)        # smooth time-decay
        mass = sal * recency
        self_score[lay] = self_score.get(lay, 0.0) + mass
        bkt = int(_epoch(r["ts"]) // 300)                    # 5-min co-activation window
        bm = buckets.setdefault(bkt, {})
        bm[lay] = bm.get(lay, 0.0) + mass
        if 0 <= int(age_h) < 24:
            per_hour[int(age_h)] = per_hour.get(int(age_h), 0) + 1
        sym = (r.get("symbol") or "").upper()
        if sym:
            sym_layers.setdefault(sym, set()).add(lay)
            sym_counts[sym] = sym_counts.get(sym, 0) + 1
            sl_score[(sym, lay)] = sl_score.get((sym, lay), 0.0) + mass

    # edges: count (co-mentions) + attention (geometric mean of both endpoints'
    # engagement on each shared symbol). Attention rewards edges where BOTH
    # layers care about the same symbol with high, recent salience.
    sym_w: dict[tuple, float] = {}
    sym_att: dict[tuple, float] = {}
    for sym, lays in sym_layers.items():
        ll = sorted(lays)
        for i in range(len(ll)):
            for j in range(i + 1, len(ll)):
                key = (ll[i], ll[j])
                sym_w[key] = sym_w.get(key, 0) + 1
                a_s = sl_score.get((sym, ll[i]), 0.0)
                b_s = sl_score.get((sym, ll[j]), 0.0)
                sym_att[key] = sym_att.get(key, 0.0) + (a_s * b_s) ** 0.5

    # temporal co-activation: layers firing in the same 5-min window form dynamic
    # edges even without a shared symbol -- "dynamic graph awareness". Weighted
    # below symbol edges (looser evidence).
    tmp_w: dict[tuple, float] = {}
    tmp_att: dict[tuple, float] = {}
    for bm in buckets.values():
        lays = sorted(bm)
        for i in range(len(lays)):
            for j in range(i + 1, len(lays)):
                key = (lays[i], lays[j])
                tmp_w[key] = tmp_w.get(key, 0) + 1
                tmp_att[key] = tmp_att.get(key, 0.0) + (bm[lays[i]] * bm[lays[j]]) ** 0.5

    # merge symbol + temporal evidence into one weighted edge set
    keys = set(sym_att) | set(tmp_att)
    raw = {k: sym_att.get(k, 0.0) + 0.6 * tmp_att.get(k, 0.0) for k in keys}
    max_att = max(raw.values(), default=0.0) or 1.0
    prune_thr = 0.15 * max_att          # PTDNet-style de-noising
    edges = []
    for k in keys:
        sa, ta = sym_att.get(k, 0.0), tmp_att.get(k, 0.0)
        kind = "both" if sa > 0 and ta > 0 else "symbol" if sa > 0 else "temporal"
        edges.append({"a": k[0], "b": k[1],
                      "weight": int(sym_w.get(k, 0) + tmp_w.get(k, 0)),
                      "attention": round(raw[k] / max_att, 3),
                      "kind": kind, "pruned": raw[k] < prune_thr})
    edges.sort(key=lambda x: x["attention"], reverse=True)

    # node influence: 1-hop message passing -- own engagement plus attention-
    # weighted neighbor engagement (a tiny GraphSAGE-style aggregation).
    adj: dict[str, list] = {}
    for e in edges:
        if e["pruned"]:
            continue
        adj.setdefault(e["a"], []).append((e["b"], e["attention"]))
        adj.setdefault(e["b"], []).append((e["a"], e["attention"]))
    influence: dict[str, float] = {}
    for lay in nodes:
        agg = sum(att * self_score.get(nb, 0.0) for nb, att in adj.get(lay, []))
        influence[lay] = self_score.get(lay, 0.0) + 0.5 * agg
    max_inf = max(influence.values(), default=0.0) or 1.0

    node_list = [{"layer": n["layer"], "count": n["count"],
                  "avg_salience": round(n["sal_sum"] / n["count"], 3), "last": n["last"],
                  "influence": round(influence.get(n["layer"], 0.0) / max_inf, 3),
                  "degree": len(adj.get(n["layer"], []))}
                 for n in nodes.values()]
    node_list.sort(key=lambda x: x["influence"], reverse=True)

    top_syms = sorted(sym_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    decay = [{"hours_ago": h, "n": per_hour.get(h, 0)} for h in range(0, 24)]
    return {"nodes": node_list, "edges": edges,
            "metrics": {"total": len(rows), "layers": len(nodes),
                        "salience": sal_hist, "decay_24h": decay,
                        "pruned_edges": sum(1 for e in edges if e["pruned"]),
                        "temporal_edges": sum(1 for e in edges if e["kind"] == "temporal"),
                        "symbol_edges": sum(1 for e in edges if e["kind"] in ("symbol", "both")),
                        "top_symbols": [{"symbol": s, "n": c} for s, c in top_syms]}}


def briefing(n: int = 12, layers=None) -> str:
    """Compact cross-layer situational summary for prompts."""
    rows = recent(n, layers)
    if not rows:
        return "(no shared insights yet)"
    return "\n".join(f"- [{r['layer']}] {r['text']}" for r in rows)


def recall(query: str) -> str:
    cl = _ltm_client()
    if not cl:
        return ""
    try:
        return cl.ask(query)
    except Exception:  # noqa: BLE001
        return ""


def snapshot() -> dict:
    """Every layer speaks: pull live state and publish it, then store a combined
    situation memo in long-term memory."""
    pub = 0
    bits = []
    # Brain
    try:
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
        pos = market_brain.cached_posture("equity")
        t = f"Regime {reg}; posture {pos.get('bias')} x{pos.get('size_mult')} ({pos.get('note','')})"
        pub += publish("brain", "regime", t, salience=0.7); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # Prediction engine
    try:
        from .predict import store as pstore
        st = pstore.stats()
        top = pstore.predictions(status="watching", limit=5)
        plans = "; ".join(f"{p['symbol']} {p['direction']} {int(p['magnitude_pct'])}%/{p['horizon_days']}d"
                          for p in top)
        t = (f"Predictions: {st['watching']} watching, {st['correct']}/{st['incorrect']} resolved, "
             f"{st['buckets']} matrix buckets. Top: {plans or 'none'}")
        pub += publish("prediction", "plans", t, salience=0.7); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # ML
    try:
        from .ml.infer import model_card
        m = model_card()
        if m.get("trained"):
            t = f"ML model AUC {m.get('auc')} edge {m.get('edge')}"
            pub += publish("ml", "model", t, salience=0.6); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # Transformer (tnet) forecast
    try:
        from . import tnet
        fc = tnet.forecast("SPY")
        if "error" not in fc:
            cal = "calibrated" if fc.get("calibrated") else "uncalibrated"
            dom = (fc.get("drivers") or {}).get("dominant")
            t = (f"Transformer: SPY {fc['direction']} p(up)={fc['prob_up']} "
                 f"conf={fc['confidence']} exp {fc['expected_move_pct']}%/{fc['horizon_days']}d "
                 f"({cal}" + (f", driver {dom}" if dom else "") + ")")
            pub += publish("tnet", "forecast", t, symbol="SPY", salience=0.65); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # Learned confluence weights (backprop)
    try:
        from . import backprop
        bc = backprop.card()
        if bc.get("trained"):
            emp = bc.get("emphasis", {}) or {}
            top = sorted(emp.items(), key=lambda kv: kv[1], reverse=True)[:3]
            t = ("Confluence weights (learned): "
                 + ", ".join(f"{k} {v:.0%}" for k, v in top)
                 + f"; acc {bc.get('accuracy', bc.get('acc', '?'))} n={bc.get('n', '?')}")
            pub += publish("backprop", "weights", t, salience=0.6); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # News aggregator: market sentiment + top catalyst
    try:
        from . import newshub
        d = newshub.aggregate()
        ms = d.get("market_sentiment", {})
        top = d.get("items", [])
        if top:
            h = top[0]
            t = (f"News net sentiment {ms.get('net')} ({ms.get('label')}); "
                 f"top catalyst: {h['title'][:90]} [{h['source']} {h['sentiment']:+.2f}]")
            pub += publish("news", "digest", t, salience=0.6); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # Neural core (cortex) calibration
    try:
        from . import cortex
        cal = cortex.calibration()
        if cal.get("trained") and cal.get("accuracy") is not None:
            t = (f"Neural core: calibrated acc {cal['accuracy']:.0%}, brier {cal.get('brier')}, "
                 f"n={cal.get('n')} ({'LIVE' if cortex.enabled() else 'shadow'})")
            pub += publish("cortex", "calibration", t, salience=0.6); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # Reasoning desk: decision throughput + dominant voices
    try:
        from . import reasoning
        rs = reasoning.stats()
        if rs.get("total"):
            lb = reasoning.voice_leaderboard()[:3]
            drivers = ", ".join(v["voice"] for v in lb) or "n/a"
            t = (f"Reasoning: {rs['total']} decisions logged, {int(rs.get('pass_rate', 0) * 100)}% "
                 f"clear the gate; lead voices {drivers}")
            pub += publish("reasoning", "summary", t, salience=0.55); bits.append(t)
    except Exception:  # noqa: BLE001
        pass
    # combined memo -> LTM (idempotent)
    if bits:
        cl = _ltm_client()
        if cl:
            try:
                cl.remember("Trading desk situation snapshot",
                            "Situation @ " + _now() + "\n- " + "\n- ".join(bits),
                            dedup_key="snapshot|" + time.strftime("%Y-%m-%d-%H", time.gmtime()))
            except Exception:  # noqa: BLE001
                pass
    return {"published": pub, "bits": bits}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print("snapshot:", snapshot())
    print("\nbriefing:\n" + briefing())
