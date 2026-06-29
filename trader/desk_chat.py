"""Desk copilot -- a fully grounded council chat.

When you ask the council a question, this first assembles a live snapshot of the
ENTIRE platform (regime + posture, the insight mesh, top predictions + decision
matrix, ML edge, the live-signal scorecard, WSB buzz, the watchlist, your open
positions) plus a Pieces LONG-TERM MEMORY recall on your question, and -- if you
mention any tickers -- a per-symbol deep dive (confluence, prediction, WSB
cross-reference, Cloudflare news sentiment). All of it is injected as grounding
so the council answers like it already knows the whole desk.
"""
from __future__ import annotations

import json
import re


def _valid_equities() -> set[str]:
    try:
        from .crsp.schema import connect
        c = connect()
        rows = c.execute("SELECT DISTINCT ticker FROM securities WHERE ticker IS NOT NULL").fetchall()
        c.close()
        return {r[0] for r in rows if r[0]}
    except Exception:  # noqa: BLE001
        return set()


_CRYPTO = {"BTC": "BTC/USD", "ETH": "ETH/USD", "SOL": "SOL/USD", "XRP": "XRP/USD",
           "DOGE": "DOGE/USD", "ADA": "ADA/USD", "AVAX": "AVAX/USD"}
_STOP = {"A", "I", "THE", "IS", "IT", "AI", "ML", "WSB", "SPY", "ETF", "CEO", "USD",
         "NOW", "BUY", "SELL", "PUT", "CALL", "YOLO"}


def _symbols_in(q: str) -> list[str]:
    out = []
    for m in re.findall(r"\$([A-Za-z]{1,5})", q):
        out.append(m.upper())
    valid = _valid_equities()
    for m in re.findall(r"\b([A-Z]{2,5})\b", q):
        if m in _CRYPTO or (m in valid and m not in _STOP):
            out.append(m)
    # keep SPY/QQQ if explicitly mentioned
    for t in ("SPY", "QQQ", "BTC", "ETH"):
        if re.search(rf"\b{t}\b", q.upper()) and t not in out:
            out.append(t)
    seen, res = set(), []
    for s in out:
        s = _CRYPTO.get(s, s)
        if s not in seen:
            seen.add(s); res.append(s)
    return res[:5]


def gather_context(q: str) -> dict:
    """Assemble the full live platform snapshot relevant to the question."""
    ctx = {"sections": [], "symbols": {}}

    def add(name, text):
        if text:
            ctx["sections"].append({"name": name, "text": text})

    # Brain
    try:
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
        pe = market_brain.cached_posture("equity"); pc = market_brain.cached_posture("crypto")
        add("REGIME", f"{reg}. equity posture {pe.get('bias')} x{pe.get('size_mult')} ({pe.get('note','')}); "
                      f"crypto posture {pc.get('bias')} x{pc.get('size_mult')}")
    except Exception:  # noqa: BLE001
        pass
    # Mesh briefing (everyone's latest)
    try:
        from . import mesh
        add("DESK MESH", mesh.briefing(12).replace("\n", " | "))
    except Exception:  # noqa: BLE001
        pass
    # Predictions + decision matrix
    try:
        from .predict import store as ps
        st = ps.stats()
        plans = "; ".join(f"{p['symbol']} {p['direction']} {int(p['magnitude_pct'])}%/{p['horizon_days']}d (rank {p['rank_score']})"
                          for p in ps.predictions(status="watching", limit=8))
        mx = "; ".join(f"{b['key']}={b['hit_rate']:.0%}(n{b['n']})" for b in ps.decision_matrix(min_n=3)[:6])
        add("PREDICTIONS", f"{st['watching']} watching, {st['correct']}/{st['incorrect']} resolved. Top: {plans or 'none'}")
        if mx:
            add("DECISION MATRIX", mx)
    except Exception:  # noqa: BLE001
        pass
    # ML
    try:
        from .ml.infer import model_card
        m = model_card()
        if m.get("trained"):
            imp = ", ".join(list((m.get("importances") or {}).keys())[:5])
            add("ML MODEL", f"AUC {m.get('auc')} edge {m.get('edge')}; top features {imp}")
    except Exception:  # noqa: BLE001
        pass
    # Signal scorecard
    try:
        from . import sigtrack
        sb = sigtrack.scoreboard().get("by_source", [])
        if sb:
            add("SIGNAL SCORECARD", "; ".join(
                f"{r['source']}: {r['signals']} sigs"
                + (f", hit {r['hit_rate']:.0%}" if r.get('hit_rate') is not None else "")
                for r in sb))
    except Exception:  # noqa: BLE001
        pass
    # WSB buzz
    try:
        from . import wsb
        b = wsb.buzz()
        tk = ", ".join(f"{t['symbol']}({t['mentions']})" for t in b.get("tickers", [])[:8])
        add("WSB BUZZ", tk or "quiet")
    except Exception:  # noqa: BLE001
        pass
    # Watchlist (armed catalysts)
    try:
        import os
        from .watchlist import WatchList  # noqa: F401
        wl_path = os.path.join(os.path.dirname(__file__), "..", "data", "watchlist.json")
        if os.path.exists(wl_path):
            items = json.load(open(wl_path)).get("items", [])
            if items:
                add("WATCHLIST", "; ".join(f"{i.get('symbol')} {i.get('thesis')}" for i in items[:8]))
    except Exception:  # noqa: BLE001
        pass
    # Positions (best-effort, paper account)
    try:
        from . import config
        from .broker import AlpacaBroker
        cfg = config.load()
        br = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret, paper=True)
        pos = br.positions_detailed()
        if pos:
            add("OPEN POSITIONS", "; ".join(
                f"{p['symbol']} {p.get('side','')} {p.get('unrealized_plpc',0):+.1f}%" for p in pos[:12]))
    except Exception:  # noqa: BLE001
        pass
    # Long-term memory recall on the question
    try:
        from . import mesh
        r = mesh.recall(q)
        if r:
            add("LONG-TERM MEMORY", r[:600])
    except Exception:  # noqa: BLE001
        pass

    # Per-symbol deep dive
    for sym in _symbols_in(q):
        d = {}
        try:
            from . import alpha
            from .crsp import query as crsp
            bars = crsp.get_prices(sym if "/" not in sym else sym, "2024-06-01", None)
            closes = [b["close"] for b in bars if b.get("close")]
            if closes:
                conv = alpha.analyze(closes, symbol=sym)
                d["confluence"] = f"{conv.side} {conv.composite:+.2f} ({'pass' if conv.gate_pass else 'block'})"
        except Exception:  # noqa: BLE001
            pass
        try:
            from .predict import engine as pe2
            f = pe2.feature_for(sym)
            if f:
                d["prediction"] = f"{f['side']} {f['score']:+.2f} ({f['n']} active)"
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import wsb
            x = wsb.cross_reference(sym.replace("/USD", ""))
            if x.get("mentions"):
                d["wsb"] = f"{x['mentions']} mentions, sentiment {x['wsb_sentiment']:+.2f}"
        except Exception:  # noqa: BLE001
            pass
        if d:
            ctx["symbols"][sym] = d
    return ctx


def context_text(ctx: dict) -> str:
    lines = [f"- {s['name']}: {s['text']}" for s in ctx["sections"]]
    for sym, d in ctx["symbols"].items():
        lines.append(f"- {sym}: " + ", ".join(f"{k}={v}" for k, v in d.items()))
    return "\n".join(lines)


def answer(cfg, q: str, symbol: str = "") -> dict:
    """Ground the council with the full desk snapshot, then deliberate."""
    from . import council
    ctx = gather_context(q + (" " + symbol if symbol else ""))
    grounding = context_text(ctx)
    aug = (f"{q}\n\n[LIVE DESK CONTEXT -- use this; it is current platform state]\n{grounding}"
           if grounding else q)
    out = council.deliberate(cfg, aug, symbol)
    out["grounding"] = [s["name"] for s in ctx["sections"]] + \
                       ([f"deep:{s}" for s in ctx["symbols"]] if ctx["symbols"] else [])
    return out


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print(json.dumps(gather_context("what should I watch today and what's the biggest risk?"),
                     indent=2)[:1500])
