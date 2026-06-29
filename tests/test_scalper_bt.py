"""Tests for the scalper backtest + walk-forward sweep (pure)."""
import math
from trader.scalper_bt import backtest, walk_forward, best_robust_params


def _oscillating(n=400, amp=0.15, period=20, base=100.0):
    return [base * (1 + amp * math.sin(2 * math.pi * i / period)) for i in range(n)]


def test_backtest_no_trades_on_flat():
    # zero-volatility series: bands collapse, must NOT trade
    assert backtest([100.0] * 200, 20, 2.0)["trades"] == 0


def test_backtest_oscillating_makes_trades():
    m = backtest(_oscillating(amp=0.2, period=30), 20, 1.0, slip_bps=10)
    assert m["trades"] >= 1
    assert "win_rate" in m and "expectancy" in m


def test_slippage_field_present():
    closes = [100.0] * 25 + [85.0] + [100.0] * 25
    m = backtest(closes, 20, 1.5, slip_bps=50)
    assert isinstance(m["expectancy"], float)


def test_walk_forward_structure():
    wf = walk_forward(_oscillating(800), train=300, test=120)
    assert "oos_total" in wf and "folds" in wf
    assert all("window" in f and "k" in f for f in wf["folds"])


def test_best_robust_params_picks_a_config():
    panel = {"A": _oscillating(800, period=18), "B": _oscillating(800, period=22)}
    r = best_robust_params(panel, train=300, test=120)
    assert r["best"] is not None
    assert r["best"]["window"] in (10, 20, 30) and r["best"]["k"] in (1.5, 2.0, 2.5)
