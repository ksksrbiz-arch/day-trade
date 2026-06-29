"""Tests for cross-sectional scoring + the walk-forward engine/metrics."""
from trader import xsection as xs
from trader.walkforward import run_engine, _metrics, _bench


def test_momentum_and_vol():
    closes = [100, 101, 102, 103, 104, 105]
    assert round(xs.momentum(closes, 5, 5), 4) == 0.05
    assert xs.momentum(closes, 2, 5) is None      # not enough history
    assert xs.volatility(closes, 5, 5) >= 0


def test_score_none_when_insufficient():
    assert xs.score([100, 101], 1, 20) is None


def test_rank_select_long_short():
    scores = {"A": 3.0, "B": 1.0, "C": -1.0, "D": -3.0}
    longs, shorts = xs.rank_select(scores, top_n=1, allow_short=True)
    assert longs == ["A"] and shorts == ["D"]


def test_rank_select_long_only():
    scores = {"A": 3.0, "B": 1.0, "C": -1.0}
    longs, shorts = xs.rank_select(scores, top_n=2, allow_short=False)
    assert longs == ["A", "B"] and shorts == []


def test_target_weights_dollar_neutral():
    w = xs.target_weights(["A", "B"], ["C", "D"])
    assert round(sum(w.values()), 6) == 0.0        # long +1, short -1
    assert round(w["A"], 3) == 0.5 and round(w["C"], 3) == -0.5


def test_metrics_basic():
    m = _metrics([0.01, 0.01, 0.01])
    assert m["days"] == 3 and m["total"] > 2.9 and m["maxdd"] == 0


def test_metrics_drawdown_negative():
    m = _metrics([0.1, -0.2, 0.05])
    assert m["maxdd"] < 0


def test_engine_runs_and_picks_trend():
    # B trends up steadily, A flat -> long-only top1 should ride B and profit
    n = 80
    prices = {"A": [100.0] * n, "B": [100.0 * (1.01 ** i) for i in range(n)]}
    params = {"lookback": 20, "vol_window": 20, "rebalance": 5, "top_n": 1,
              "allow_short": False, "slippage_bps": 10}
    rets = run_engine(prices, ["A", "B"], 30, n, params)
    assert len(rets) == n - 30
    assert _metrics(rets)["total"] > 0            # captured the uptrend


def test_bench_length():
    spy = [100.0 + i for i in range(20)]
    assert len(_bench(spy, 5, 20)) == 15
