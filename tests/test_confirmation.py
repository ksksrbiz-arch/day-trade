"""Tests for the deterministic confirmation layer: feature math + the gate."""
from trader.marketdata import compute_features, Features
from trader.strategy import confirm_intent, Intent, StrategyConfig
from trader.context import MarketContext, _coerce


# ---- compute_features (pure) ----

def _series(start, step, n):
    return [start + step * i for i in range(n)]


def test_features_none_on_short_history():
    assert compute_features("X", [1, 2, 3], [10, 10, 10]) is None


def test_features_uptrend_above_sma():
    closes = _series(100.0, 1.0, 25)         # steady rise
    vols = [1000.0] * 24 + [2000.0]          # volume spike on last bar
    f = compute_features("AAPL", closes, vols)
    assert f is not None
    assert f.ret_20d > 0
    assert f.above_sma20 is True
    assert f.rvol > 1.5                       # 2000 vs ~1000 avg


def test_features_downtrend_below_sma():
    closes = _series(150.0, -1.0, 25)         # steady decline
    vols = [1000.0] * 25
    f = compute_features("XYZ", closes, vols)
    assert f.ret_20d < 0
    assert f.above_sma20 is False


# ---- confirm_intent (pure deterministic gate) ----

def _intent(side="buy"):
    return Intent(symbol="AAPL", side=side, notional=1000.0,
                  take_profit_pct=0.05, stop_loss_pct=0.03)


def test_confirmation_off_always_passes():
    cfg = StrategyConfig(require_confirmation=False)
    ok, _ = confirm_intent(_intent(), None, None, cfg)
    assert ok is True


def test_fail_open_when_no_features():
    cfg = StrategyConfig(require_confirmation=True, confirm_fail_open=True)
    ok, _ = confirm_intent(_intent(), None, None, cfg)
    assert ok is True


def test_fail_closed_when_no_features():
    cfg = StrategyConfig(require_confirmation=True, confirm_fail_open=False)
    ok, _ = confirm_intent(_intent(), None, None, cfg)
    assert ok is False


def test_groq_veto_blocks():
    cfg = StrategyConfig(require_confirmation=True)
    f = Features("AAPL", 25, 120.0, 0.03, 0.10, 0.01, 1.2, True)
    ctx = MarketContext(confirm=False, note="contradiction")
    ok, reason = confirm_intent(_intent("buy"), f, ctx, cfg)
    assert ok is False and "veto" in reason


def test_thin_volume_blocks():
    cfg = StrategyConfig(require_confirmation=True, min_rvol=0.5)
    f = Features("AAPL", 25, 120.0, 0.03, 0.10, 0.01, 0.30, True)  # rvol 0.30
    ok, reason = confirm_intent(_intent("buy"), f, MarketContext(), cfg)
    assert ok is False and "thin volume" in reason


def test_long_against_downtrend_blocks():
    cfg = StrategyConfig(require_confirmation=True, momentum_tolerance=0.08)
    f = Features("AAPL", 25, 90.0, -0.05, -0.20, 0.02, 1.0, False)  # deep downtrend, below SMA
    ok, reason = confirm_intent(_intent("buy"), f, MarketContext(), cfg)
    assert ok is False and "downtrend" in reason


def test_clean_long_confirms():
    cfg = StrategyConfig(require_confirmation=True)
    f = Features("AAPL", 25, 130.0, 0.04, 0.12, 0.01, 1.3, True)
    ok, reason = confirm_intent(_intent("buy"), f, MarketContext(confirm=True), cfg)
    assert ok is True and "confirmed" in reason


def test_short_against_uptrend_blocks():
    cfg = StrategyConfig(require_confirmation=True)
    f = Features("AAPL", 25, 140.0, 0.05, 0.20, 0.01, 1.1, True)  # strong uptrend
    ok, reason = confirm_intent(_intent("sell"), f, MarketContext(), cfg)
    assert ok is False and "uptrend" in reason


# ---- groq output coercion (tolerant parse) ----

def test_coerce_tolerates_messy_json():
    ctx = _coerce({"regime": "RISK_ON", "trend_alignment": "yes",
                   "risk_flags": "high_volatility", "confirm": 1})
    assert ctx.regime == "risk_on"
    assert ctx.risk_flags == ["high_volatility"]
    assert ctx.confirm is True
