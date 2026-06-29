"""Tests for the survival guardrails: RR, circuit breaker, trailing stop."""
from trader.risk import (enforce_rr, RiskState, roll_day, circuit_breaker,
                         trailing_stop)


# ---- 1:2 reward:risk ----

def test_rr_tightens_wide_stop():
    tp, sl = enforce_rr(0.04, 0.03, min_ratio=2.0)   # 0.04/0.03 = 1.33 < 2
    assert tp == 0.04 and sl == 0.02                 # stop tightened to tp/2


def test_rr_leaves_good_ratio():
    tp, sl = enforce_rr(0.06, 0.02, min_ratio=2.0)   # already 3:1
    assert (tp, sl) == (0.06, 0.02)


# ---- circuit breaker ----

def test_breaker_trips_on_drawdown():
    st = RiskState(day_start_equity=100000, day="2026-06-22")
    tripped, dd = circuit_breaker(96900, st, max_dd_pct=3.0)   # -3.1%
    assert tripped is True and dd < -3.0


def test_breaker_holds_within_limit():
    st = RiskState(day_start_equity=100000, day="2026-06-22")
    tripped, dd = circuit_breaker(98500, st, max_dd_pct=3.0)   # -1.5%
    assert tripped is False


def test_roll_day_resets():
    st = RiskState(day_start_equity=90000, tripped=True, day="2026-06-21")
    st2 = roll_day(st, 100000, "2026-06-22")
    assert st2.day_start_equity == 100000 and st2.tripped is False


def test_roll_day_same_day_noop():
    st = RiskState(day_start_equity=100000, tripped=True, day="2026-06-22")
    st2 = roll_day(st, 95000, "2026-06-22")
    assert st2 is st


# ---- trailing stop ----

def test_trailing_long_ratchets_up():
    stop1, hwm1 = trailing_stop(100, 100, "buy", 0.02)        # hwm 100 -> stop 98
    assert stop1 == 98.0 and hwm1 == 100
    stop2, hwm2 = trailing_stop(100, 110, "buy", 0.02, hwm1)  # hwm 110 -> stop 107.8
    assert hwm2 == 110 and stop2 == 107.8
    stop3, hwm3 = trailing_stop(100, 105, "buy", 0.02, hwm2)  # hwm stays 110
    assert hwm3 == 110 and stop3 == 107.8


def test_trailing_short_ratchets_down():
    stop1, hwm1 = trailing_stop(100, 100, "sell", 0.02)       # hwm 100 -> stop 102
    assert stop1 == 102.0 and hwm1 == 100
    stop2, hwm2 = trailing_stop(100, 90, "sell", 0.02, hwm1)  # hwm 90 -> stop 91.8
    assert hwm2 == 90 and stop2 == 91.8
