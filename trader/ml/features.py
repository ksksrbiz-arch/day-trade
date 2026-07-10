"""Canonical feature vector -- the single source of feature math for both
training (dataset.py) and live scoring (infer.py). Identical code path => no
train/serve skew.

All features are derived purely from a price history (oldest -> newest) using
the already-tested ta + quant engines, then normalized to roughly [-1, 1].
"""
from __future__ import annotations

from .. import ta as _ta
from .. import quant as _q

FEATURES = [
    "rsi", "macd", "pctb", "stoch", "ema_cross", "mom20",
    "trend", "atr", "sharpe", "zsma", "persist",
    "mom60", "mom120", "volratio", "relmom20", "relmom60",
]


def _ret_n(closes, n):
    """Simple return over the last n bars (0.0 if not enough history)."""
    if not closes or len(closes) <= n or not closes[-n - 1]:
        return 0.0
    return closes[-1] / closes[-n - 1] - 1.0


def _clamp(x, lo=-1.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return 0.0


def feature_vector(closes: list[float], bench_closes: list[float] | None = None):
    """Return (vector, names) or (None, FEATURES) if too little history."""
    if not closes or len(closes) < 30:
        return None, FEATURES
    s = _ta.ta_signals(closes)
    ns = _q.name_stats(closes)
    if s is None:
        return None, FEATURES
    rsi = (s.rsi14 - 50) / 50 if s.rsi14 is not None else 0.0
    macd = s.components.get("macd", 0.0)            # already normalized vote
    pctb = (s.pctb - 0.5) * 2 if s.pctb is not None else 0.0
    stoch = (s.stoch_k - 50) / 50 if s.stoch_k is not None else 0.0
    ema_cross = s.components.get("ema_cross", 0.0)
    mom20 = s.components.get("momentum", 0.0)
    trend = (s.trend_strength * 2 - 1)             # 0..1 -> -1..1
    atr = _clamp((s.atr_pct or 0.0) * 20)          # ~0..1 scaled
    sharpe = _clamp((ns.sharpe_20 if ns else 0.0) / 6)
    zsma = _clamp((ns.z_vs_sma20 if ns else 0.0) / 3)
    persist = _clamp(ns.persistence if ns else 0.0)
    # long-horizon momentum (the classic 12-1 factor, in trading days): 60-day
    # return, and 120->20 "skip-recent" momentum that avoids short-term reversal.
    mom60 = _clamp(_ret_n(closes, 60) / 0.30)
    mom120 = 0.0
    if len(closes) > 121:
        m = closes[-21] / closes[-121] - 1.0 if closes[-121] else 0.0
        mom120 = _clamp(m / 0.40)
    # volatility ratio: recent (20d) vs longer (60d) realized vol -> regime/expansion
    volratio = 0.0
    try:
        import statistics as _st
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1]]
        if len(rets) >= 60:
            sr = _st.pstdev(rets[-20:]); lr = _st.pstdev(rets[-60:])
            if lr:
                volratio = _clamp((sr / lr - 1.0))
    except Exception:  # noqa: BLE001
        pass
    # relative strength vs the market proxy -- the practical cross-sectional
    # signal (a stock beating the market is the momentum/alpha the model targets).
    # Self-contained + identical in train and serve (both pass a benchmark window),
    # so there is no train/serve skew; abstains to 0 when no benchmark is supplied.
    relmom20 = relmom60 = 0.0
    if bench_closes and len(bench_closes) >= 61 and len(closes) >= 61:
        relmom20 = _clamp((_ret_n(closes, 20) - _ret_n(bench_closes, 20)) / 0.15)
        relmom60 = _clamp((_ret_n(closes, 60) - _ret_n(bench_closes, 60)) / 0.25)
    vec = [_clamp(rsi), _clamp(macd), _clamp(pctb), _clamp(stoch),
           _clamp(ema_cross), _clamp(mom20), _clamp(trend), atr,
           sharpe, zsma, persist, mom60, mom120, volratio, relmom20, relmom60]
    return vec, FEATURES


if __name__ == "__main__":
    up = [100 * (1.01 ** i) for i in range(80)]
    v, names = feature_vector(up)
    print(dict(zip(names, [round(x, 3) for x in v])))
