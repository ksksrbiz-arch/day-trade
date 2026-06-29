"""Tests for the Market Brain's pure feature math + regime classifier."""
from trader.market_brain import trend_score, realized_vol, correlation, classify, build_features


def _up(n=220, step=0.5, base=100.0):
    return [base + step * i for i in range(n)]


def _down(n=220, step=0.5, base=200.0):
    return [base - step * i for i in range(n)]


def test_trend_score_up_and_down():
    assert trend_score(_up()) == 2
    assert trend_score(_down()) == -2
    assert trend_score([100.0] * 5) == 0       # too short


def test_realized_vol_nonneg():
    assert realized_vol(_up()) >= 0


def test_correlation_perfect_positive():
    a = _up(60); b = _up(60)
    assert correlation(a, b, 30) > 0.9


def test_classify_high_vol_overrides():
    assert classify({"spy_vol_pct": 0.9, "equity_trend": 2, "risk_appetite": 3, "breadth": 0.9}) == "high_vol"


def test_classify_risk_on():
    assert classify({"spy_vol_pct": 0.4, "equity_trend": 2, "risk_appetite": 2, "breadth": 0.7}) == "risk_on"


def test_classify_risk_off():
    assert classify({"spy_vol_pct": 0.5, "equity_trend": -2, "risk_appetite": -2, "breadth": 0.3}) == "risk_off"


def test_classify_neutral():
    assert classify({"spy_vol_pct": 0.5, "equity_trend": 0, "risk_appetite": 0, "breadth": 0.5}) == "neutral"


def test_build_features_shapes():
    prices = {"SPY": _up(), "QQQ": _up(), "IWM": _up(), "TLT": _down(),
              "GLD": _down(), "UUP": _down(), "HYG": _up()}
    crypto = {"BTC/USD": _up(), "ETH/USD": _up()}
    f = build_features(prices, crypto)
    assert "regime" in f and "equity_trend" in f and "breadth" in f
    assert f["spy_trend"] == 2 and f["regime"] in ("risk_on", "neutral", "risk_off", "high_vol")
