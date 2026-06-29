"""Technical-analysis engine.

Pure functions of a price history (oldest -> newest). Same input, same output,
no network, no randomness -- so every indicator is unit-testable and replays
identically in backtests, exactly like strategy.decide().

Indicators: SMA/EMA trend, RSI, MACD, Bollinger %b, ATR (close proxy when no
OHLC), stochastic %K, and trailing momentum. These are combined into a single
confluence score in [-1, 1] (with a human label) so the rest of the system can
treat "technicals" as one auditable number while still logging the components.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import fmean, pstdev


# --------------------------------------------------------------------------- #
# primitive indicators (pure)                                                  #
# --------------------------------------------------------------------------- #
def sma(xs: list[float], n: int) -> float | None:
    if len(xs) < n or n <= 0:
        return None
    return fmean(xs[-n:])


def ema(xs: list[float], n: int) -> float | None:
    if len(xs) < n or n <= 0:
        return None
    k = 2 / (n + 1)
    e = fmean(xs[:n])
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = fmean(gains), fmean(losses)
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram) or (None, None, None)."""
    if len(closes) < slow + signal:
        return None, None, None
    # build a short macd-line series to derive the signal EMA
    line_series = []
    for end in range(slow, len(closes) + 1):
        sub = closes[:end]
        ef, es = ema(sub, fast), ema(sub, slow)
        if ef is None or es is None:
            continue
        line_series.append(ef - es)
    if len(line_series) < signal:
        return None, None, None
    line = line_series[-1]
    sig = ema(line_series, signal)
    if sig is None:
        return None, None, None
    return line, sig, line - sig


def bollinger_pctb(closes: list[float], n: int = 20, k: float = 2.0) -> float | None:
    """%b: 0 = lower band, 1 = upper band; <0 or >1 = outside the bands."""
    if len(closes) < n:
        return None
    win = closes[-n:]
    mid = fmean(win)
    sd = pstdev(win)
    if sd == 0:
        return 0.5
    upper, lower = mid + k * sd, mid - k * sd
    return (closes[-1] - lower) / (upper - lower)


def atr_pct(closes: list[float], highs: list[float] | None = None,
            lows: list[float] | None = None, n: int = 14) -> float | None:
    """ATR as a fraction of price. Uses true range when OHLC supplied, else a
    close-to-close proxy."""
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        if highs and lows and len(highs) == len(closes) and len(lows) == len(closes):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
        else:
            tr = abs(closes[i] - closes[i - 1])
        trs.append(tr)
    atr = fmean(trs)
    return atr / closes[-1] if closes[-1] else None


def stochastic_k(closes: list[float], highs: list[float] | None = None,
                 lows: list[float] | None = None, n: int = 14) -> float | None:
    if len(closes) < n:
        return None
    hi = max(highs[-n:]) if highs and len(highs) == len(closes) else max(closes[-n:])
    lo = min(lows[-n:]) if lows and len(lows) == len(closes) else min(closes[-n:])
    if hi == lo:
        return 50.0
    return 100 * (closes[-1] - lo) / (hi - lo)


def momentum(closes: list[float], n: int) -> float | None:
    if len(closes) <= n or closes[-1 - n] == 0:
        return None
    return closes[-1] / closes[-1 - n] - 1.0


# --------------------------------------------------------------------------- #
# composite                                                                    #
# --------------------------------------------------------------------------- #
@dataclass
class TASignals:
    n: int
    rsi14: float | None
    macd_hist: float | None
    pctb: float | None
    stoch_k: float | None
    atr_pct: float | None
    mom_20: float | None
    ema_cross: float | None       # (ema12-ema26)/price, >0 bullish
    trend_strength: float         # 0=ranging .. 1=strong trend (directional consistency)
    score: float                  # composite confluence in [-1, 1]
    label: str                    # strong_sell..strong_buy
    components: dict               # per-indicator vote in [-1,1]

    def as_log(self) -> dict:
        d = asdict(self)
        d.pop("components", None)
        return d


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def ta_signals(closes: list[float], highs: list[float] | None = None,
               lows: list[float] | None = None) -> TASignals | None:
    """Compute all indicators + a confluence score. None if too little data."""
    if not closes or len(closes) < 15:
        return None
    price = closes[-1]
    r = rsi(closes, 14)
    _, _, hist = macd(closes)
    pb = bollinger_pctb(closes, 20, 2.0)
    sk = stochastic_k(closes, highs, lows, 14)
    at = atr_pct(closes, highs, lows, 14)
    m20 = momentum(closes, 20)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    ema_cross = ((e12 - e26) / price) if (e12 and e26 and price) else None

    # directional consistency over last ~20 days -> trend strength in [0,1].
    rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 20), len(closes))
            if closes[i - 1]]
    if rets:
        signs = sum(1 if x > 0 else -1 if x < 0 else 0 for x in rets)
        trend_strength = _clamp(abs(signs) / len(rets), 0.0, 1.0)
    else:
        trend_strength = 0.0

    votes: dict[str, float] = {}
    trend_votes: dict[str, float] = {}      # trend-following: trust when trending
    mr_votes: dict[str, float] = {}         # mean-reversion: trust when ranging
    # RSI: oversold (<30) bullish, overbought (>70) bearish (mean-reversion).
    if r is not None:
        mr_votes["rsi"] = _clamp((50 - r) / 20)
    # MACD histogram: sign = direction (trend).
    if hist is not None and price:
        trend_votes["macd"] = _clamp((hist / price) * 200)
    # Bollinger %b: near lower band bullish, near upper bearish (mean-reversion).
    if pb is not None:
        mr_votes["bbands"] = _clamp((0.5 - pb) * 2)
    # Stochastic: <20 bullish, >80 bearish (mean-reversion).
    if sk is not None:
        mr_votes["stoch"] = _clamp((50 - sk) / 30)
    # EMA cross: trend confirmation.
    if ema_cross is not None:
        trend_votes["ema_cross"] = _clamp(ema_cross * 50)
    # Momentum: 20d trend.
    if m20 is not None:
        trend_votes["momentum"] = _clamp(m20 * 5)
    votes = {**trend_votes, **mr_votes}

    # Regime-aware blend: weight trend votes by trend_strength, mean-reversion by
    # (1 - trend_strength). This stops oscillators vetoing a clean trend (and
    # stops momentum chasing inside a range) -- how a real trader reads a chart.
    t = fmean(trend_votes.values()) if trend_votes else 0.0
    mrv = fmean(mr_votes.values()) if mr_votes else 0.0
    if trend_votes and mr_votes:
        score = _clamp(trend_strength * t + (1 - trend_strength) * mrv)
    elif trend_votes:
        score = _clamp(t)
    else:
        score = _clamp(mrv)
    label = ("strong_buy" if score >= 0.5 else "buy" if score >= 0.15 else
             "strong_sell" if score <= -0.5 else "sell" if score <= -0.15 else "neutral")
    return TASignals(
        n=len(closes),
        rsi14=round(r, 2) if r is not None else None,
        macd_hist=round(hist, 5) if hist is not None else None,
        pctb=round(pb, 3) if pb is not None else None,
        stoch_k=round(sk, 2) if sk is not None else None,
        atr_pct=round(at, 4) if at is not None else None,
        mom_20=round(m20, 4) if m20 is not None else None,
        ema_cross=round(ema_cross, 5) if ema_cross is not None else None,
        trend_strength=round(trend_strength, 3),
        score=round(score, 3), label=label,
        components={k: round(v, 3) for k, v in votes.items()},
    )


if __name__ == "__main__":
    import math
    up = [100 * (1.01 ** i) for i in range(60)]
    down = [100 * (0.99 ** i) for i in range(60)]
    osc = [100 + 5 * math.sin(i / 3) for i in range(60)]
    for name, series in [("uptrend", up), ("downtrend", down), ("oscillating", osc)]:
        s = ta_signals(series)
        print(f"{name:12s} score={s.score:+.3f} {s.label:11s} trend={s.trend_strength:.2f} rsi={s.rsi14} {s.components}")
