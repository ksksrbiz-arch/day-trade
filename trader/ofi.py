"""
Order-Flow Imbalance (OFI) -- a top-of-book pressure signal.

imbalance = (bid_size - ask_size) / (bid_size + ask_size), in [-1, +1].
  +1  => all resting size is on the bid (buyers stacked) -> upward pressure
  -1  => all on the ask (sellers stacked)               -> downward pressure

HONEST SCOPE: real OFI alpha lives in the millisecond book churn that colocated
firms see. Polled over REST against a paper account you are NOT capturing
seconds-scale micro-pumps -- treat this as a *confirming* pressure reading on a
minutes horizon, never as a low-latency edge. The pure math is here and tested;
the data fetch (in marketdata) is best-effort.
"""
from __future__ import annotations

from typing import Optional


def ofi(bid_size: float, ask_size: float) -> float:
    tot = (bid_size or 0) + (ask_size or 0)
    if tot <= 0:
        return 0.0
    return round(((bid_size or 0) - (ask_size or 0)) / tot, 4)


def ofi_signal(imbalance: float, threshold: float = 0.6) -> Optional[str]:
    """Map an imbalance to a direction, or None if pressure is unclear."""
    if imbalance >= threshold:
        return "buy"
    if imbalance <= -threshold:
        return "sell"
    return None


def confirms(side: str, imbalance: float, min_align: float = 0.0) -> bool:
    """Does book pressure agree with a proposed side? Used as a soft gate.

    A buy wants imbalance >= -min_align (not stacked against it); a sell the
    mirror. With min_align=0 it just checks the sign isn't opposed.
    """
    if side == "buy":
        return imbalance >= -min_align
    return imbalance <= min_align
