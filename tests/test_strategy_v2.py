"""Tests for v2 deterministic strategy upgrades: regime, sizing, adaptive exits."""
from trader.marketdata import Features
from trader.labels import Label
from trader.strategy import (Intent, StrategyConfig, confirm_intent,
                             size_and_exits, market_regime)


def feat(ret20=0.05, vol=0.02, rvol=1.2, above=True):
    return Features("AAPL", 25, 120.0, 0.02, ret20, vol, rvol, above)


def lab(sent=0.7, conf=0.8):
    return Label(tickers=["AAPL"], sentiment=sent, confidence=conf, event_type="earnings")


def intent(side="buy", notional=1000.0):
    return Intent(symbol="AAPL", side=side, notional=notional,
                  take_profit_pct=0.05, stop_loss_pct=0.03)


# ---- market_regime ----

def test_regime_risk_on():
    assert market_regime(feat(ret20=0.05, above=True)) == "risk_on"


def test_regime_risk_off():
    assert market_regime(feat(ret20=-0.05, above=False)) == "risk_off"


def test_regime_neutral_mixed():
    assert market_regime(feat(ret20=0.05, above=False)) == "neutral"


def test_regime_none():
    assert market_regime(None) == "neutral"


# ---- regime gate in confirm_intent ----

def test_long_blocked_in_risk_off():
    cfg = StrategyConfig(require_confirmation=True, regime_filter=True)
    ok, why = confirm_intent(intent("buy"), feat(), None, cfg, market_regime="risk_off")
    assert ok is False and "risk_off" in why


def test_short_blocked_in_risk_on():
    cfg = StrategyConfig(require_confirmation=True, regime_filter=True)
    ok, why = confirm_intent(intent("sell"), feat(ret20=-0.01), None, cfg, market_regime="risk_on")
    assert ok is False and "risk_on" in why


def test_regime_off_does_not_block():
    cfg = StrategyConfig(require_confirmation=True, regime_filter=False)
    ok, _ = confirm_intent(intent("buy"), feat(), None, cfg, market_regime="risk_off")
    assert ok is True


# ---- dynamic sizing ----

def test_sizing_disabled_is_noop():
    cfg = StrategyConfig(dynamic_sizing=False, notional_per_trade=1000)
    out = size_and_exits(intent(notional=1000), lab(), feat(), cfg)
    assert out.notional == 1000.0


def test_strong_low_vol_sizes_up():
    cfg = StrategyConfig(dynamic_sizing=True, notional_per_trade=1000,
                         vol_target=0.02, size_max_mult=2.0)
    out = size_and_exits(intent(), lab(sent=0.9, conf=0.9), feat(vol=0.01), cfg)
    assert out.notional > 1000  # strong signal + calm name -> larger


def test_high_vol_sizes_down():
    cfg = StrategyConfig(dynamic_sizing=True, notional_per_trade=1000,
                         vol_target=0.02, size_min_mult=0.5)
    out = size_and_exits(intent(), lab(sent=0.5, conf=0.6), feat(vol=0.08), cfg)
    assert out.notional < 1000  # volatile name -> smaller


def test_sizing_respects_clamp():
    cfg = StrategyConfig(dynamic_sizing=True, notional_per_trade=1000,
                         vol_target=0.02, size_max_mult=1.5)
    out = size_and_exits(intent(), lab(sent=1.0, conf=1.0), feat(vol=0.001), cfg)
    assert out.notional <= 1500.0 + 1e-6  # capped at max_mult


# ---- adaptive exits ----

def test_adaptive_exits_from_vol():
    cfg = StrategyConfig(adaptive_exits=True, tp_vol_mult=2.5, sl_vol_mult=1.5,
                         tp_floor=0.02, tp_cap=0.15, sl_floor=0.015, sl_cap=0.08)
    out = size_and_exits(intent(), lab(), feat(vol=0.03), cfg)
    assert out.take_profit_pct == 0.075   # 2.5*0.03
    assert out.stop_loss_pct == 0.045     # 1.5*0.03


def test_adaptive_exits_clamped():
    cfg = StrategyConfig(adaptive_exits=True, tp_vol_mult=2.5, sl_vol_mult=1.5,
                         tp_cap=0.15, sl_cap=0.08)
    out = size_and_exits(intent(), lab(), feat(vol=0.20), cfg)
    assert out.take_profit_pct == 0.15    # capped
    assert out.stop_loss_pct == 0.08      # capped
