"""Mesh consensus -> tradeable signal (hermetic).

These tests stub ``trader.mesh_consensus.consensus`` with crafted dicts so they
are fully deterministic and never touch the real mesh.
"""
from trader import mesh_consensus, mesh_signal


def _stub(monkeypatch, symbols):
    payload = {"symbols": list(symbols), "generated": "2026-06-27T00:00:00Z"}
    monkeypatch.setattr(mesh_consensus, "consensus", lambda *a, **k: payload)


def test_strong_up_consensus_positive_signal(monkeypatch):
    _stub(monkeypatch, [
        {
            "symbol": "SPY",
            "mentions": 5,
            "layers": ["brain", "tnet", "ml"],
            "net": 0.6,
            "direction": "up",
            "agree": 3,
            "salience": 3.5,
        },
    ])
    # agree 3 -> breadth 1.0; mentions 5 -> depth 1.0; confidence 1.0
    # signal == net == 0.6
    sig = mesh_signal.consensus_signal("spy")  # case-insensitive
    assert sig is not None
    assert sig > 0
    assert abs(sig - 0.6) < 1e-9


def test_unknown_symbol_returns_none(monkeypatch):
    _stub(monkeypatch, [
        {
            "symbol": "SPY",
            "mentions": 5,
            "layers": ["brain", "tnet", "ml"],
            "net": 0.6,
            "direction": "up",
            "agree": 3,
            "salience": 3.5,
        },
    ])
    assert mesh_signal.consensus_signal("ZZZZ") is None


def test_signals_maps_symbols_to_floats(monkeypatch):
    _stub(monkeypatch, [
        {
            "symbol": "SPY",
            "mentions": 5,
            "layers": ["brain", "tnet", "ml"],
            "net": 0.6,
            "direction": "up",
            "agree": 3,
            "salience": 3.5,
        },
        {
            "symbol": "IWM",
            "mentions": 4,
            "layers": ["brain", "news"],
            "net": -0.4,
            "direction": "down",
            "agree": 2,
            "salience": 1.2,
        },
        # Only one layer agrees -> skipped from signals().
        {
            "symbol": "QQQ",
            "mentions": 2,
            "layers": ["brain", "tnet"],
            "net": 0.05,
            "direction": "flat",
            "agree": 1,
            "salience": 0.5,
        },
    ])
    res = mesh_signal.signals()
    assert "generated" in res
    assert isinstance(res["signals"], dict)
    assert set(res["signals"].keys()) == {"SPY", "IWM"}
    for sym, val in res["signals"].items():
        assert isinstance(val, float)
    assert res["signals"]["SPY"] > 0
    assert res["signals"]["IWM"] < 0
