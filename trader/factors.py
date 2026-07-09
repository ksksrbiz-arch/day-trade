"""
Cross-sectional factor voice -- the platform's first *relative* alpha.

Every other voice forecasts a single symbol's ABSOLUTE direction from its own
history -- one of the hardest, most efficiently-priced things to predict. This
voice instead ranks the whole universe against itself each day and trades the
SPREAD, which is where the few robustly-documented equity anomalies live:

  * momentum (12-1)      -- past winners keep winning (skip the last month to
                            avoid short-term reversal contamination).
  * short-term reversal  -- last week's biggest movers tend to snap back.
  * low volatility       -- lower-vol names earn better risk-adjusted returns.
  * trend                -- price above its own longer average.

Each raw factor is Z-SCORED ACROSS THE UNIVERSE (cross-sectional), the z's are
combined, and a symbol's composite z maps through tanh to a signal in [-1,1]:
positive = top of the cross-section (go long), negative = bottom (go short).
Pure NumPy; the universe panel is fetched once (cached) per refresh.
"""
from __future__ import annotations

import time

import numpy as np

UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "NFLX", "AVGO", "CRM", "ORCL", "ADBE", "JPM", "BAC", "XOM", "UNH",
    "WMT", "COST", "PEP", "KO", "DIS", "INTC", "CSCO", "QCOM", "TXN", "HYG",
]
# factor -> weight in the composite (sign already oriented so + = bullish)
_WEIGHTS = {"mom": 0.40, "reversal": 0.20, "lowvol": 0.15, "trend": 0.25}
_cache = {"ts": 0.0, "scores": {}, "detail": {}}
_TTL = 900.0


def _ret(closes, a, b) -> float:
    if len(closes) <= max(a, b) or closes[-1 - b] == 0:
        return 0.0
    return closes[-1 - b] and (closes[-1 - a] / closes[-1 - b] - 1.0)


def _raw_factors(closes: list[float]) -> dict | None:
    if len(closes) < 70:
        return None
    c = np.asarray(closes, dtype=float)
    rets = np.diff(c) / (c[:-1] + 1e-12)
    mom = float(c[-21] / c[-min(len(c) - 1, 120)] - 1.0)     # ~5mo-to-1mo momentum (12-1 analogue)
    reversal = -float(c[-1] / c[-6] - 1.0)                   # negative of last-week move
    lowvol = -float(np.std(rets[-20:]))                      # negative realized vol
    trend = float(c[-1] / np.mean(c[-50:]) - 1.0)            # above/below 50d average
    return {"mom": mom, "reversal": reversal, "lowvol": lowvol, "trend": trend}


def _z(vals: dict[str, float]) -> dict[str, float]:
    xs = np.asarray(list(vals.values()), dtype=float)
    mu, sd = float(xs.mean()), float(xs.std())
    if sd == 0:
        return {k: 0.0 for k in vals}
    return {k: (v - mu) / sd for k, v in vals.items()}


def _refresh(universe=None) -> None:
    from .ml.dataset import _alpaca_series
    uni = universe or UNIVERSE
    raw: dict[str, dict] = {}
    for s in uni:
        try:
            ser = _alpaca_series(s)
        except Exception:  # noqa: BLE001
            ser = []
        f = _raw_factors([c for _, c in ser]) if ser else None
        if f:
            raw[s] = f
    if len(raw) < 5:
        _cache.update(ts=time.time(), scores={}, detail={})
        return
    # cross-sectional z-score EACH factor across the universe, then combine
    zbyf = {fac: _z({s: raw[s][fac] for s in raw}) for fac in _WEIGHTS}
    scores, detail = {}, {}
    for s in raw:
        comp = sum(_WEIGHTS[fac] * zbyf[fac][s] for fac in _WEIGHTS)
        scores[s] = round(float(np.tanh(comp)), 4)          # -> [-1,1]
        detail[s] = {fac: round(zbyf[fac][s], 2) for fac in _WEIGHTS}
    _cache.update(ts=time.time(), scores=scores, detail=detail)


def score_signal(symbol: str, ttl: float = _TTL) -> float | None:
    """Cross-sectional factor signal in [-1,1] for the confluence brain, or None.
    Mirrors tnet.score_signal so it drops into analyze() as a new voice."""
    sym = symbol.upper()
    if time.time() - _cache["ts"] > ttl or not _cache["scores"]:
        try:
            _refresh()
        except Exception:  # noqa: BLE001
            return None
    return _cache["scores"].get(sym)


def ranking(universe=None) -> list[dict]:
    """Full cross-sectional table (for the terminal / an endpoint)."""
    if time.time() - _cache["ts"] > _TTL or not _cache["scores"]:
        _refresh(universe)
    out = [{"symbol": s, "score": sc, "factors": _cache["detail"].get(s, {})}
           for s, sc in _cache["scores"].items()]
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def arm_top(n: int = 3, wl=None, allow_short: bool = True, expiry_min: int = 1440,
            min_abs: float = 0.30) -> dict:
    """ARM the strongest cross-sectional names into the watch->wait->strike list:
    the top of the ranking as longs, the bottom as shorts (if allowed). The bot
    only strikes when price CONFIRMS the factor thesis. `wl` shares a trade loop's
    WatchList so arming is in-process."""
    from .ml.dataset import _alpaca_series
    rank = ranking()
    if not rank:
        return {"ok": False, "armed": 0, "reason": "no ranking"}
    if wl is None:
        from .watchlist import WatchList
        wl = WatchList()
    picks = [(c, "buy") for c in rank[:n] if c["score"] >= min_abs]
    if allow_short:
        picks += [(c, "sell") for c in rank[-n:] if c["score"] <= -min_abs]
    armed = []
    for c, side in picks:
        try:
            ser = _alpaca_series(c["symbol"])
            if not ser:
                continue
            px = float(ser[-1][1])
            wl.arm(c["symbol"], side, px, f"factor {side} z={c['score']}",
                   buffer=0.005, expiry_min=expiry_min,
                   confidence=min(0.95, abs(c["score"])), source="factor")
            armed.append({"symbol": c["symbol"], "side": side, "score": c["score"]})
        except Exception:  # noqa: BLE001
            continue
    return {"ok": True, "armed": armed}


if __name__ == "__main__":
    import json
    print(json.dumps(ranking()[:8], indent=2))
