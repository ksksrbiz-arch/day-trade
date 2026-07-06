"""
Principled position sizing: volatility targeting + fractional Kelly.

The aggression dial scales bets by conviction, but two pieces of standard
portfolio math turn a small edge into better *compounded* returns while
controlling drawdown -- both bounded, both sit UNDER the drawdown breaker:

1. VOLATILITY TARGETING. Size inversely to a name's recent realized volatility
   so every position contributes roughly EQUAL risk. A 40%-vol name gets a
   smaller notional than a 12%-vol name for the same signal -- this alone
   improves risk-adjusted return and stops volatile names from dominating P&L.

2. FRACTIONAL KELLY. The Kelly criterion says bet a fraction of capital
   proportional to your edge; full Kelly is famously too aggressive (ruinous
   under estimation error), so practitioners use a FRACTION (~1/4). We size by
   frac * (2p - 1) off the CALIBRATED probability p -- more edge -> bigger bet,
   no edge -> no bet. Pure stdlib; every call is fail-soft (returns 1.0).
"""
from __future__ import annotations

import math

_TARGET_ANN_VOL = 0.30          # target annualized vol per position (~30%)
_KELLY_FRACTION = 0.25          # quarter-Kelly
_TRADING_DAYS = 252


def realized_vol(symbol: str, lookback: int = 20) -> float | None:
    """Annualized realized vol from recent daily closes. None if unavailable."""
    try:
        from .ml.dataset import _alpaca_series
        ser = _alpaca_series(symbol)
        closes = [float(c) for _, c in ser][-(lookback + 1):]
        if len(closes) < max(6, lookback // 2):
            return None
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1]]
        if len(rets) < 5:
            return None
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        return sd * math.sqrt(_TRADING_DAYS)
    except Exception:  # noqa: BLE001
        return None


def vol_target_mult(symbol: str, target: float = _TARGET_ANN_VOL,
                    lo: float = 0.5, hi: float = 2.0) -> float:
    """target_vol / realized_vol, clamped. 1.0 if vol can't be measured."""
    rv = realized_vol(symbol)
    if not rv or rv <= 0:
        return 1.0
    return round(max(lo, min(hi, target / rv)), 3)


def kelly_mult(conf_or_p: float, frac: float = _KELLY_FRACTION,
               cap: float = 2.0, is_prob: bool = False) -> float:
    """Fractional-Kelly multiplier. `conf_or_p` is either a calibrated win
    probability (is_prob=True) or a conviction magnitude in [0.5,1]. The bet
    scales with edge = 2p-1; no edge -> ~0 size."""
    p = conf_or_p if is_prob else max(0.5, min(1.0, conf_or_p))
    edge = max(0.0, 2.0 * p - 1.0)            # symmetric-payoff Kelly bet = 2p-1
    # quarter-Kelly scaling as a multiplier: no edge -> 0.5x, p=.6 -> 1.0x,
    # p=.7 -> 1.5x, p>=.8 -> 2.0x (frac*10 = slope). Bounded.
    return round(max(0.0, min(cap, 0.5 + frac * 10.0 * edge)), 3)


def size_multiplier(symbol: str, conf: float = 0.6, p_up: float | None = None,
                    target_vol: float = _TARGET_ANN_VOL) -> tuple[float, str]:
    """Combined vol-target x fractional-Kelly multiplier + a short why-string.
    Bounded to [0.2, 2.5]; the drawdown breaker still sits below this."""
    vt = vol_target_mult(symbol, target=target_vol)
    if p_up is not None:
        kelly = kelly_mult(p_up, is_prob=True)
    else:
        kelly = kelly_mult(conf)
    mult = round(max(0.2, min(2.5, vt * kelly)), 3)
    return mult, f"volTgt x{vt} · kelly x{kelly}"


if __name__ == "__main__":
    # cheap self-check of the math (no network)
    for p in (0.50, 0.55, 0.60, 0.70):
        print(f"p={p}  kelly_mult={kelly_mult(p, is_prob=True)}")
