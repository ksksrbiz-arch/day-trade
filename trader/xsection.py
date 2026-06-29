"""
Cross-sectional ranking strategy (signal combination #4).

Instead of reacting to one name's news, we score the ENTIRE universe each
rebalance and hold the best vs (optionally) short the worst. The score combines
two factors that don't require speed -- the kind of edge a patient, broad
retail system can actually access:

  * momentum      : trailing return over `lookback` days
  * risk-adjust   : divide by realized volatility (favor steadier movers)

score = momentum / (vol + eps)  -> "vol-adjusted momentum"

All pure functions of price history -> fully backtestable, no lookahead.
"""
from __future__ import annotations

from statistics import pstdev
from typing import Optional

EPS = 1e-6


def momentum(closes: list[float], i: int, lookback: int) -> Optional[float]:
    """Trailing return ending at index i (inclusive), or None if not enough data."""
    if i < lookback or closes[i - lookback] <= 0:
        return None
    return closes[i] / closes[i - lookback] - 1.0


def volatility(closes: list[float], i: int, window: int) -> float:
    """Stdev of daily returns over the trailing `window` ending at i."""
    lo = max(1, i - window + 1)
    rets = []
    for j in range(lo, i + 1):
        if closes[j - 1] > 0:
            rets.append(closes[j] / closes[j - 1] - 1.0)
    return pstdev(rets) if len(rets) >= 2 else 0.0


def score(closes: list[float], i: int, lookback: int, vol_window: int = 20) -> Optional[float]:
    m = momentum(closes, i, lookback)
    if m is None:
        return None
    v = volatility(closes, i, vol_window)
    return m / (v + EPS)


def rank_select(scores: dict[str, float], top_n: int, allow_short: bool):
    """Return (longs, shorts) symbol lists from a score map.

    Longs = highest `top_n` scores; shorts = lowest `top_n` (only if allow_short).
    Symbols with None scores are excluded.
    """
    ranked = sorted(((s, v) for s, v in scores.items() if v is not None),
                    key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return [], []
    n = min(top_n, len(ranked) // 2 if allow_short else len(ranked))
    n = max(1, n)
    longs = [s for s, _ in ranked[:n]]
    shorts = [s for s, _ in ranked[-n:]] if allow_short else []
    # never long and short the same name
    shorts = [s for s in shorts if s not in longs]
    return longs, shorts


def target_weights(longs: list[str], shorts: list[str]):
    """Equal-weight, dollar-neutral when shorting. Returns {symbol: weight}."""
    w = {}
    if longs:
        wl = 1.0 / len(longs)
        for s in longs:
            w[s] = wl
    if shorts:
        ws = 1.0 / len(shorts)
        for s in shorts:
            w[s] = w.get(s, 0.0) - ws
    return w
