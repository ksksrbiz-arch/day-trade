"""Tests for the deeper brain: crypto regime, confidence, posture (pure)."""
from trader.market_brain import crypto_classify, regime_confidence, posture


def test_crypto_regime_risk_on():
    assert crypto_classify({"btc_trend": 2, "btc_mom_20": 5.0, "crypto_breadth_24h": 0.8}) == "risk_on"


def test_crypto_regime_risk_off():
    assert crypto_classify({"btc_trend": -2, "btc_mom_20": -5.0, "crypto_breadth_24h": 0.2}) == "risk_off"


def test_crypto_regime_neutral():
    assert crypto_classify({"btc_trend": 1, "btc_mom_20": -1.0, "crypto_breadth_24h": 0.5}) == "neutral"


def test_confidence_high_when_aligned():
    f = {"regime": "risk_on", "spy_trend": 2, "qqq_trend": 2, "iwm_trend": 1,
         "hyg_trend": 1, "uup_trend": -1, "spy_vol_pct": 0.4}
    assert regime_confidence(f) >= 0.8


def test_confidence_eroded_by_high_vol():
    f = {"regime": "risk_on", "spy_trend": 2, "qqq_trend": 2, "iwm_trend": 1,
         "hyg_trend": 1, "uup_trend": -1, "spy_vol_pct": 0.95}
    assert regime_confidence(f) < 0.8


def test_posture_high_vol_halves_size():
    p = posture("high_vol", 0.9)
    assert p["size_mult"] == 0.5 and p["bias"] == "neutral"


def test_posture_risk_on_scales_up_long():
    p = posture("risk_on", 1.0)
    assert p["bias"] == "long" and p["size_mult"] > 1.0


def test_posture_risk_off_defensive_short():
    p = posture("risk_off", 0.5)
    assert p["bias"] == "short" and p["size_mult"] < 1.0


def test_posture_neutral_baseline():
    p = posture("neutral", 0.5)
    assert p["size_mult"] == 1.0
