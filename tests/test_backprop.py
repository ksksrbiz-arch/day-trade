"""Tests for the confluence backprop learner (math + persistence, no network)."""
import json
import numpy as np
from trader import backprop as bp


def test_no_data_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "DECISIONS", str(tmp_path / "d.jsonl"))
    monkeypatch.setattr(bp, "WEIGHTS", str(tmp_path / "w.json"))
    r = bp.train()
    assert r["ok"] is False and "30" in r["reason"]


def test_log_decision_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "DECISIONS", str(tmp_path / "d.jsonl"))
    monkeypatch.setattr(bp, "_DATA", str(tmp_path))
    s = {"ta": 0.5, "quant": -0.2}
    assert bp.log_decision("AAPL", s, day="2026-01-02", ref_price=100) is True
    assert bp.log_decision("AAPL", s, day="2026-01-02", ref_price=101) is False  # same sym/day


def test_backprop_learns_and_reduces_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "WEIGHTS", str(tmp_path / "w.json"))
    monkeypatch.setattr(bp, "HISTORY", str(tmp_path / "h.json"))
    monkeypatch.setattr(bp, "_DATA", str(tmp_path))
    # separable: 'ta'(0) up-predictive, 'ml'(3) down-predictive
    rng = np.random.default_rng(0)
    X = rng.normal(size=(240, len(bp.METHODS)))
    y = (X[:, 0] - X[:, 3] > 0).astype(float)
    monkeypatch.setattr(bp, "build_dataset", lambda: (X, y))
    # avoid network publish
    import sys, types
    monkeypatch.setitem(sys.modules, "trader.mesh", types.SimpleNamespace(publish=lambda *a, **k: None))
    r = bp.train(epochs=500, lr=0.3, l2=0.1)
    assert r["ok"] and r["loss"] < r["loss_start"]      # gradient descent reduced loss
    assert r["accuracy"] > 0.8
    w = r["weights"]
    assert w["ta"] > 0 and w["ml"] < 0                  # recovered signs
    emph = r["emphasis"]
    assert abs(sum(emph.values()) - 1.0) < 0.01           # softmax normalized (rounded)
    assert emph["ml"] <= emph["ta"]                     # ReLU drops anti-predictive
    assert bp.learned_emphasis()["ta"] > 0              # persisted + reloadable
