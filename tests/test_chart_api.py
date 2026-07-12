"""Tests for the Advanced Chart (GP) terminal router.

Dependency-free: no TestClient/httpx. Route mounting is asserted through the
OpenAPI schema (router routes are hidden from ``app.routes`` in this FastAPI
version), and keyless-safety is proven by calling the handler directly.
"""
from dashboard.app import app
from dashboard.api.chart import get_chart, _build_studies, _vwap


def test_route_mounted():
    assert "/api/chart/{symbol}" in set(app.openapi()["paths"])


def test_handler_keyless():
    out = get_chart("SPY")
    assert isinstance(out, dict)
    assert out["symbol"] == "SPY"
    # keyless env -> structured empty shape, never a crash
    assert out["candles"] == []
    assert out["studies"] == {}


def test_handler_lowercase_symbol_normalized():
    out = get_chart("spy", days=30)
    assert out["symbol"] == "SPY"
    assert out["days"] == 30
    assert isinstance(out["candles"], list)
    assert isinstance(out["studies"], dict)


def test_build_studies_shape():
    # synthetic uptrend candles -> studies align to candle count
    candles = [
        {"t": f"2026-01-{i+1:02d}", "o": 100 + i, "h": 101 + i,
         "l": 99 + i, "c": 100 + i, "v": 1000.0 + i}
        for i in range(40)
    ]
    studies = _build_studies(candles)
    for key in ("sma20", "ema12", "ema26", "bb_upper", "bb_lower",
                "vwap", "rsi14", "macd", "macd_hist"):
        assert key in studies
        assert len(studies[key]) == len(candles)
    # VWAP is defined from the first bar when volume is present
    assert _vwap(candles)[0] is not None


def test_build_studies_empty():
    assert _build_studies([]) == {}
