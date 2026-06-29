"""
Mean-reversion scalper -- a PRICE-driven (not news-driven) deterministic signal.

Thesis: a name that has violently deviated from its short-term mean tends to snap
back. We measure deviation with Bollinger bands (SMA +/- k*stdev). A close below
the lower band is a counter-trend BUY (bet on snap-up); above the upper band is a
counter-trend SELL.

All pure -> fully backtestable. Reality check (kept honest): on minute bars polled
over REST this is a minutes-horizon mean-reversion, not microsecond scalping. The
slippage haircut in the backtest will punish anything that pretends otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Optional


@dataclass
class Bands:
    mid: float
    upper: float
    lower: float
    z: float        # how many stdevs the last close sits from the mean


def bollinger(closes: list[float], window: int = 20, k: float = 2.0) -> Optional[Bands]:
    if len(closes) < window:
        return None
    win = closes[-window:]
    mid = fmean(win)
    sd = pstdev(win)
    last = closes[-1]
    z = (last - mid) / sd if sd > 0 else 0.0
    return Bands(mid=round(mid, 4), upper=round(mid + k * sd, 4),
                 lower=round(mid - k * sd, 4), z=round(z, 3))


def scalper_signal(closes: list[float], window: int = 20, k: float = 2.0,
                   exit_z: float = 0.3) -> Optional[str]:
    """Return 'buy' (oversold snapback), 'sell' (overbought snapback), or None.

    A signal fires only when the last close is OUTSIDE the band (|z| >= k).
    exit_z is advisory for the caller (mean reached when |z| <= exit_z).
    """
    b = bollinger(closes, window, k)
    if b is None:
        return None
    last = closes[-1]
    if last <= b.lower:
        return "buy"
    if last >= b.upper:
        return "sell"
    return None
