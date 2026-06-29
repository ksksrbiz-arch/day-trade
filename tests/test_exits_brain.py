"""Brain-aware risk exit: cut offside losers in a defensive regime (hermetic)."""
from trader import exits


def _posture(monkeypatch, asset_map):
    import trader.market_brain as mb
    monkeypatch.setattr(mb, "cached_posture", lambda asset="equity": asset_map.get(asset, {}))


def test_offside_long_in_defensive_crypto(monkeypatch):
    _posture(monkeypatch, {"crypto": {"bias": "risk_off", "size_mult": 0.75}})
    why = exits._brain_offside("LTCUSD", "buy", "crypto", -0.012, 41.0, 40.5, 0.004)
    assert why and "offside" in why


def test_neutral_regime_no_exit(monkeypatch):
    _posture(monkeypatch, {"crypto": {"bias": "neutral", "size_mult": 1.0}})
    assert exits._brain_offside("LTCUSD", "buy", "crypto", -0.012, 41.0, 40.5, 0.004) is None


def test_small_loss_below_floor_no_exit(monkeypatch):
    _posture(monkeypatch, {"crypto": {"bias": "risk_off", "size_mult": 0.75}})
    assert exits._brain_offside("LTCUSD", "buy", "crypto", -0.001, 41.0, 40.96, 0.004) is None


def test_short_offside_in_risk_on_equity(monkeypatch):
    _posture(monkeypatch, {"equity": {"bias": "risk_on", "size_mult": 1.2}})
    why = exits._brain_offside("GOOGL", "sell", "us_equity", -0.02, 340.0, 346.8, 0.004)
    assert why and "offside" in why


def test_winning_position_not_cut(monkeypatch):
    _posture(monkeypatch, {"crypto": {"bias": "risk_off", "size_mult": 0.75}})
    # long but in profit -> not an offside loser
    assert exits._brain_offside("BTCUSD", "buy", "crypto", +0.03, 100.0, 103.0, 0.004) is None
