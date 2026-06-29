"""Prediction engine: ingest -> rank -> watch -> resolve -> learn, and expose a
calibrated directional view (feature_for) to the ML + execution layers.
"""
from __future__ import annotations

import time

from . import store
from . import hypothesis

_px_cache: dict[str, tuple] = {}


# ---- price lookups -------------------------------------------------------- #
def _equity_series(sym):
    from ..crsp import query as crsp
    bars = crsp.get_prices(sym, "2024-01-01", None)
    return [(b["date"], b["close"]) for b in bars if b.get("close")]


def _crypto_series(sym, ttl=600):
    now = time.time()
    if sym in _px_cache and now - _px_cache[sym][0] < ttl:
        return _px_cache[sym][1]
    try:
        from .. import history
        p = history.load_panel([sym], days=400, source="coinex")
        ser = list(zip(p["dates"], p["prices"].get(sym, [])))
    except Exception:  # noqa: BLE001
        ser = []
    _px_cache[sym] = (now, ser)
    return ser


def _series(sym, asset):
    return _crypto_series(sym) if asset == "crypto" else _equity_series(sym)


def _last_price(sym, asset):
    s = _series(sym, asset)
    return s[-1][1] if s else None


def _price_fn(sym, asset, day, horizon):
    s = _series(sym, asset)
    if not s:
        return None
    ds = [d for d, _ in s]; cl = [c for _, c in s]
    idx = next((i for i, d in enumerate(ds) if d >= day), None)
    if idx is None or idx + horizon >= len(cl):
        return None
    return (cl[idx], cl[idx + horizon])


# ---- pipeline ------------------------------------------------------------- #
def ingest_wsb(max_posts=25) -> dict:
    """Pull WSB chatter, extract hypotheses, rank via the matrix, store as watched plans."""
    try:
        from .. import wsb, market_brain
        regime = market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        regime = "neutral"
    items = wsb.fetch_items()[:max_posts]
    snippets = [(it.get("title", "") + " " + it.get("summary", "")) for it in items]
    hyps = hypothesis.extract(snippets)
    created, plans = 0, []
    for h in hyps:
        ref = _last_price(h["symbol"], h["asset"])
        pid, made = store.record_prediction(
            "wsb", h["symbol"], h["direction"], h["horizon_days"], h["magnitude_pct"],
            h["rationale"], h["confidence"], regime, ref, asset=h["asset"])
        if made:
            created += 1
        if pid:
            plans.append({**h, "id": pid, "ref_price": ref})
    return {"posts": len(items), "hypotheses": len(hyps), "new_plans": created,
            "plans": plans}


def resolve() -> dict:
    return store.resolve_due(_price_fn)


def feature_for(symbol: str) -> dict | None:
    """Prediction-layer directional view for a symbol, for execution/ML.
    Blends active watched predictions (rank-weighted) into a score in [-1,1]."""
    symbol = symbol.upper()
    preds = [p for p in store.predictions(status="watching", limit=200)
             if p["symbol"] == symbol]
    if not preds:
        return None
    num = den = 0.0
    for p in preds:
        s = (1 if p["direction"] == "up" else -1) * (p["rank_score"] or 0.5)
        num += s; den += abs(p["rank_score"] or 0.5)
    score = round(num / den, 3) if den else 0.0
    return {"symbol": symbol, "score": score, "n": len(preds),
            "side": "buy" if score > 0 else "sell" if score < 0 else "flat"}


def score_signal(symbol: str):
    """Scalar in [-1,1] for confluence (None if no active prediction)."""
    f = feature_for(symbol)
    return f["score"] if f else None


def cycle() -> dict:
    r = resolve()
    ing = ingest_wsb()
    return {"resolved": r.get("resolved", 0), "new_plans": ing["new_plans"],
            "hypotheses": ing["hypotheses"], "stats": store.stats()}


if __name__ == "__main__":
    import json
    print(json.dumps(cycle(), indent=2, default=str))
    print("top watched plans:")
    for p in store.predictions(status="watching", limit=8):
        print(f"  {p['symbol']:8s} {p['direction']:4s} {p['magnitude_pct']:.0f}% "
              f"{p['horizon_days']}d rank={p['rank_score']} :: {p['rationale'][:50]}")
