"""Unified situational awareness -- the single shared picture of the whole
platform that EVERY layer reasons from (agents, council, prediction, reflex,
desk copilot). One cached snapshot per ~minute so all layers in a cycle see the
exact same state cheaply, making them act like one system instead of silos.

  snapshot() -> {"sections":[{name,text}], "ts"}   (cached)
  brief(max_lines) -> compact multi-line string for prompts
"""
from __future__ import annotations

import time

_cache = {"ts": 0.0, "data": None}
TTL = 60.0


def _gather() -> dict:
    sections = []

    def add(name, text):
        if text:
            sections.append({"name": name, "text": str(text)[:400]})

    try:
        from . import tnet
        fc = tnet.forecast("SPY")
        if "error" not in fc:
            cal = "calibrated" if fc.get("calibrated") else "uncal"
            dom = (fc.get("drivers") or {}).get("dominant")
            add("FORECAST", f"Transformer: SPY {fc['direction']} p(up) {fc['prob_up']:.0%} "
                            f"conf {fc['confidence']:.0%}, exp {fc['expected_move_pct']:+.2f}%/"
                            f"{fc['horizon_days']}d [{cal}]"
                            + (f"; driven by {dom}" if dom else ""))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import backprop
        bc = backprop.card()
        if bc.get("trained"):
            emp = bc.get("emphasis", {}) or {}
            top = sorted(emp.items(), key=lambda kv: kv[1], reverse=True)[:4]
            add("WEIGHTS", "Confluence (learned): "
                + ", ".join(f"{k} {v:.0%}" for k, v in top))
    except Exception:  # noqa: BLE001
        pass
    # forward-edge + voice-attribution summaries (published by the ML daemon; cheap mesh reads)
    try:
        from . import mesh
        er = mesh.recent(1, layers=["edge"])
        if er:
            add("EDGE", er[0]["text"])
        ar = mesh.recent(1, layers=["attribution"])
        if ar:
            add("ATTRIBUTION", ar[0]["text"])
        sh = mesh.recent(1, layers=["shadow"])
        if sh:
            add("SHADOW_LAB", sh[0]["text"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import newshub
        d = newshub.aggregate()
        ms = d.get("market_sentiment", {})
        top = d.get("items", [])[:2]
        heads = "; ".join(f"{it['title'][:60]} ({it['sentiment']:+.2f})" for it in top)
        add("NEWS", f"{d['counts']} sources, net sentiment {ms.get('net')} ({ms.get('label')}). "
                    f"Top: {heads}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
        pe = market_brain.cached_posture("equity"); pc = market_brain.cached_posture("crypto")
        add("REGIME", f"{reg}. equity {pe.get('bias')} x{pe.get('size_mult')} ({pe.get('note','')}); "
                      f"crypto {pc.get('bias')} x{pc.get('size_mult')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .predict import store as ps
        st = ps.stats()
        plans = "; ".join(f"{p['symbol']} {p['direction']} {int(p['magnitude_pct'])}%/{p['horizon_days']}d"
                          for p in ps.predictions(status="watching", limit=6))
        add("PREDICTIONS", f"{st['watching']} watching, {st['correct']}/{st['incorrect']} resolved, "
                           f"{st['buckets']} matrix buckets. Top: {plans or 'none'}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .ml.infer import model_card
        m = model_card()
        if m.get("trained"):
            add("ML", f"AUC {m.get('auc')} edge {m.get('edge')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import sigtrack
        sb = sigtrack.scoreboard().get("by_source", [])
        if sb:
            add("SIGNALS", "; ".join(
                f"{r['source']}:{r['signals']}" + (f"@{r['hit_rate']:.0%}" if r.get('hit_rate') is not None else "")
                for r in sb))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import wsb
        tk = ", ".join(f"{t['symbol']}({t['mentions']})" for t in wsb.buzz().get("tickers", [])[:6])
        add("WSB", tk or "quiet")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import config
        from .broker import AlpacaBroker
        cfg = config.load()
        br = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret, paper=True)
        pos = br.positions_detailed()
        if pos:
            add("POSITIONS", f"{len(pos)} open: " + ", ".join(
                f"{p['symbol']}{p.get('unrealized_plpc',0):+.0f}%" for p in pos[:10]))
    except Exception:  # noqa: BLE001
        pass
    return {"sections": sections, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def snapshot(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["data"] and now - _cache["ts"] < TTL:
        return _cache["data"]
    _cache["data"] = _gather(); _cache["ts"] = now
    return _cache["data"]


def brief(max_lines: int = 8) -> str:
    snap = snapshot()
    return "\n".join(f"- {s['name']}: {s['text']}" for s in snap["sections"][:max_lines])


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print(brief())
