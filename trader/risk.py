"""
Risk guardrails -- the "survival rules". All pure and unit-tested; the loop and
broker call into them. None of this fabricates an edge; it prevents one bad day
from erasing twenty good trades.

  * enforce_rr        -- guarantee reward:risk >= a floor (default 2:1) by
                         tightening the stop relative to the take-profit.
  * RiskState         -- tracks day-start equity + a tripped kill switch.
  * circuit_breaker   -- trips when intraday drawdown breaches a hard threshold.
  * trailing_stop     -- pure trailing-stop price given a high-water mark.
"""
from __future__ import annotations

from dataclasses import dataclass


def enforce_rr(take_profit_pct: float, stop_loss_pct: float,
               min_ratio: float = 2.0) -> tuple[float, float]:
    """Return (tp, sl) such that tp/sl >= min_ratio.

    Reward:risk must be at least min_ratio. If the stop is too wide for the
    target, tighten the stop to tp / min_ratio. TP is left unchanged.
    """
    tp = max(0.0, float(take_profit_pct))
    sl = max(1e-9, float(stop_loss_pct))
    if tp / sl < min_ratio:
        sl = round(tp / min_ratio, 4)
    return tp, sl


@dataclass
class RiskState:
    day_start_equity: float
    tripped: bool = False
    day: str = ""          # YYYY-MM-DD the day_start_equity belongs to


def roll_day(state: RiskState, equity: float, today: str) -> RiskState:
    """Reset the breaker at the start of a new trading day."""
    if state.day != today:
        return RiskState(day_start_equity=equity, tripped=False, day=today)
    return state


def circuit_breaker(equity: float, state: RiskState, max_dd_pct: float) -> tuple[bool, float]:
    """Return (tripped, drawdown_pct_from_day_start).

    drawdown is negative when down. Trips when drawdown <= -max_dd_pct.
    max_dd_pct is expressed as a positive percent (e.g. 3.0 = 3%).
    """
    base = state.day_start_equity or equity
    if base <= 0:
        return False, 0.0
    dd = (equity / base - 1.0) * 100.0
    return (dd <= -abs(max_dd_pct)), round(dd, 3)


def trailing_stop(entry: float, last: float, side: str,
                  trail_pct: float, hwm: float | None = None) -> tuple[float, float]:
    """Pure trailing stop. Returns (stop_price, new_high_water_mark).

    For a long: hwm tracks the highest price since entry; stop = hwm*(1-trail).
    For a short: hwm tracks the lowest price; stop = hwm*(1+trail).
    Pass the previous hwm back in each tick to ratchet it.
    """
    trail = abs(trail_pct)
    if side == "buy":
        hwm = last if hwm is None else max(hwm, last)
        return round(hwm * (1 - trail), 4), hwm
    else:
        hwm = last if hwm is None else min(hwm, last)
        return round(hwm * (1 + trail), 4), hwm
