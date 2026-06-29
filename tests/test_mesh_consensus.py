"""Cross-layer mesh consensus engine (hermetic).

These tests stub ``trader.mesh.recent`` with crafted insight lists so they are
fully deterministic and never touch the real mesh database.
"""
from trader import mesh, mesh_consensus


def _stub(monkeypatch, rows):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: list(rows))


def test_three_layers_agree_up(monkeypatch):
    rows = [
        {"layer": "brain", "symbol": "SPY", "salience": 0.8, "text": "SPY risk_on rally"},
        {"layer": "tnet", "symbol": "SPY", "salience": 0.6, "text": "SPY forecast up"},
        {"layer": "ml", "symbol": "SPY", "salience": 0.5, "text": "SPY long buy signal"},
    ]
    _stub(monkeypatch, rows)
    res = mesh_consensus.consensus()
    syms = {s["symbol"]: s for s in res["symbols"]}
    assert "SPY" in syms
    spy = syms["SPY"]
    assert spy["direction"] == "up"
    assert spy["net"] > 0.15
    assert spy["mentions"] == 3
    assert set(spy["layers"]) == {"brain", "tnet", "ml"}
    assert spy["agree"] >= 2
    assert "generated" in res


def test_conflicting_layers_are_flat(monkeypatch):
    # Two equally-weighted opposing votes from distinct layers -> net ~ 0.
    rows = [
        {"layer": "brain", "symbol": "QQQ", "salience": 0.7, "text": "QQQ breakout up"},
        {"layer": "tnet", "symbol": "QQQ", "salience": 0.7, "text": "QQQ breakdown down"},
    ]
    _stub(monkeypatch, rows)
    res = mesh_consensus.consensus()
    syms = {s["symbol"]: s for s in res["symbols"]}
    assert "QQQ" in syms
    qqq = syms["QQQ"]
    assert qqq["direction"] == "flat"
    assert abs(qqq["net"]) <= 0.15


def test_single_layer_symbol_excluded(monkeypatch):
    # AAPL has 2 mentions but only ONE distinct layer -> excluded.
    # NVDA has 2 mentions across 2 layers -> included (control).
    rows = [
        {"layer": "news", "symbol": "AAPL", "salience": 0.9, "text": "AAPL surge up"},
        {"layer": "news", "symbol": "AAPL", "salience": 0.8, "text": "AAPL rally gain"},
        {"layer": "brain", "symbol": "NVDA", "salience": 0.7, "text": "NVDA up long"},
        {"layer": "ml", "symbol": "NVDA", "salience": 0.6, "text": "NVDA buy bull"},
    ]
    _stub(monkeypatch, rows)
    res = mesh_consensus.consensus()
    out = {s["symbol"] for s in res["symbols"]}
    assert "AAPL" not in out
    assert "NVDA" in out


def test_top_n_and_sorting(monkeypatch):
    rows = [
        # strong, unanimous up -> high abs(net)*mentions
        {"layer": "brain", "symbol": "SPY", "salience": 0.9, "text": "SPY up"},
        {"layer": "tnet", "symbol": "SPY", "salience": 0.9, "text": "SPY long"},
        {"layer": "ml", "symbol": "SPY", "salience": 0.9, "text": "SPY buy"},
        # mild down
        {"layer": "brain", "symbol": "IWM", "salience": 0.4, "text": "IWM down"},
        {"layer": "news", "symbol": "IWM", "salience": 0.3, "text": "IWM drop"},
    ]
    _stub(monkeypatch, rows)
    t = mesh_consensus.top(1)
    assert len(t) == 1
    assert t[0]["symbol"] == "SPY"
    assert mesh_consensus.top(0) == []


def test_empty_is_safe(monkeypatch):
    _stub(monkeypatch, [])
    res = mesh_consensus.consensus()
    assert res["symbols"] == []
    assert "generated" in res
    assert mesh_consensus.top() == []
