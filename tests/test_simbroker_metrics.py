import math

from trader.simbroker import SimBroker, SimConfig, ClosedTrade
from trader import metrics


# ---------- SimBroker: the haircut must always hurt ----------

def test_buy_fills_higher_than_reference():
    b = SimBroker(SimConfig(slippage_bps=100))  # 1%
    pos = b.open("X", notional=100, ref_price=100.0, side="long")
    assert pos.entry_price == 101.0  # paid 1% more


def test_sell_fills_lower_than_reference():
    b = SimBroker(SimConfig(slippage_bps=100))
    b.open("X", notional=100, ref_price=100.0, side="long")
    trade = b.close("X", ref_price=100.0)
    assert trade.exit_price == 99.0  # received 1% less


def test_round_trip_at_flat_price_loses_money():
    b = SimBroker(SimConfig(slippage_bps=50))  # 0.5% each side
    b.open("X", notional=100, ref_price=100.0, side="long")
    trade = b.close("X", ref_price=100.0)
    assert trade.pnl < 0  # flat price but you still paid the spread twice


def test_winning_long_trade_pnl_positive():
    b = SimBroker(SimConfig(slippage_bps=0))
    b.open("X", notional=100, ref_price=100.0, side="long")
    trade = b.close("X", ref_price=110.0)
    assert math.isclose(trade.pnl, 10.0, rel_tol=1e-9)


def test_short_profits_when_price_falls():
    b = SimBroker(SimConfig(slippage_bps=0))
    b.open("X", notional=100, ref_price=100.0, side="short")
    trade = b.close("X", ref_price=90.0)
    assert trade.pnl > 0


# ---------- metrics ----------

def test_max_drawdown():
    assert math.isclose(metrics.max_drawdown([100, 120, 90, 110]), 0.25, rel_tol=1e-9)
    assert metrics.max_drawdown([100, 101, 102]) == 0.0
    assert metrics.max_drawdown([]) == 0.0


def test_summarize_basic():
    trades = [
        ClosedTrade("A", "long", 1, 100, 110),  # +10
        ClosedTrade("B", "long", 1, 100, 95),   # -5
        ClosedTrade("C", "long", 1, 100, 105),  # +5
    ]
    curve = [100, 110, 105, 110]
    stats = metrics.summarize(trades, curve, benchmark_return=0.02)
    assert stats["trades"] == 3
    assert math.isclose(stats["win_rate"], 2 / 3, rel_tol=1e-9)
    assert math.isclose(stats["avg_win"], 7.5, rel_tol=1e-9)   # (10+5)/2
    assert math.isclose(stats["avg_loss"], 5.0, rel_tol=1e-9)
    assert math.isclose(stats["profit_factor"], 15 / 5, rel_tol=1e-9)
    assert math.isclose(stats["expectancy"], 10 / 3, rel_tol=1e-9)
    # total return 10% vs benchmark 2% -> +8%
    assert math.isclose(stats["vs_benchmark"], 0.08, rel_tol=1e-9)


def test_summarize_empty_is_safe():
    stats = metrics.summarize([], [100], benchmark_return=0.05)
    assert stats["trades"] == 0
    assert stats["vs_benchmark"] == -0.05  # did nothing, benchmark rose
