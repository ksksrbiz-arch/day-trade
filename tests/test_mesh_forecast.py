"""Mesh layer forecasting (hermetic).

Monkeypatch trader.mesh.recent so no DB or live mesh is touched. We craft a
NEWEST-FIRST list that, once reversed into chronological order, encodes a clear
repeating pattern: brain -> tnet -> ml, brain -> tnet -> ml, ...
"""
from trader import mesh, mesh_forecast


def _fake_recent_newest_first():
    """Chronological pattern is brain, tnet, ml repeated 4x.

    The mesh returns newest-first, so we build it chronologically and reverse.
    """
    chrono = ["brain", "tnet", "ml"] * 4  # brain,tnet,ml,brain,tnet,ml,...
    rows = []
    for i, layer in enumerate(chrono):
        rows.append({
            "id": str(i), "ts": "t", "day": "d", "layer": layer,
            "kind": "k", "symbol": "", "salience": 0.5, "text": "x",
        })
    rows.reverse()  # now NEWEST FIRST, matching mesh.recent's contract
    return rows


def test_predict_next_ranks_tnet_after_brain(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_recent_newest_first())
    preds = mesh_forecast.predict_next("brain")
    assert isinstance(preds, list) and preds
    assert preds[0]["layer"] == "tnet"
    assert preds[0]["prob"] > 0.5


def test_transitions_tnet_to_ml_is_high(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_recent_newest_first())
    out = mesh_forecast.transitions()
    assert "matrix" in out and "counts" in out and "generated" in out
    assert out["counts"] > 0
    assert out["matrix"]["tnet"]["ml"] >= 0.99


def test_most_likely_is_a_dominant_transition(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_recent_newest_first())
    ml = mesh_forecast.most_likely()
    assert ml and "from" in ml and "to" in ml and "prob" in ml
    # the pattern's deterministic edges all have prob ~1.0
    assert (ml["from"], ml["to"]) in {("brain", "tnet"), ("tnet", "ml"), ("ml", "brain")}
    assert ml["prob"] >= 0.99


def test_unseen_layer_returns_empty(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_recent_newest_first())
    assert mesh_forecast.predict_next("does_not_exist") == []


def test_failsoft_on_broken_mesh(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("mesh down")
    monkeypatch.setattr(mesh, "recent", boom)
    assert mesh_forecast.transitions()["counts"] == 0
    assert mesh_forecast.predict_next("brain") == []
    assert mesh_forecast.most_likely() == {}
