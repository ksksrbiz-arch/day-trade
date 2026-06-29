"""Tests for the Neural Core (cortex) MLP decision fuser."""
import numpy as np
from trader import cortex


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex, "WEIGHTS", str(tmp_path / "c.npz"))
    monkeypatch.setattr(cortex, "CARD", str(tmp_path / "card.json"))
    monkeypatch.setattr(cortex, "_DATA", str(tmp_path))
    cortex._cache.update(ts=0.0, params=None, loaded=False)


def test_untrained_is_neutral(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = cortex.conviction({m: 0.5 for m in cortex.METHODS})
    assert c["trained"] is False and c["conviction"] == 0.0


def test_learns_nonlinear_xor(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(cortex.METHODS)))
    y = ((X[:, 0] > 0) ^ (X[:, 3] > 0)).astype(float)   # XOR -> not linearly separable
    import trader.backprop as bp
    monkeypatch.setattr(bp, "build_dataset", lambda: (X, y))
    r = cortex.train(min_samples=30)
    assert r["trained"] is True
    assert r["val_acc"] > 0.85          # a linear model cannot exceed ~0.5 here


def test_conviction_range_and_persistence(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, len(cortex.METHODS)))
    y = (X[:, 0] + X[:, 1] > 0).astype(float)
    import trader.backprop as bp
    monkeypatch.setattr(bp, "build_dataset", lambda: (X, y))
    cortex.train(min_samples=30)
    assert cortex.card()["trained"] is True
    c = cortex.conviction({m: 0.6 for m in cortex.METHODS})
    assert c["trained"] is True and -1.0 <= c["conviction"] <= 1.0


def test_ensemble_confidence_and_saliency(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    rng = np.random.default_rng(2)
    X = rng.normal(size=(320, len(cortex.METHODS)))
    y = ((X[:, 0] > 0) ^ (X[:, 3] > 0)).astype(float)        # XOR over ta & ml
    import trader.backprop as bp
    monkeypatch.setattr(bp, "build_dataset", lambda: (X, y))
    r = cortex.train(min_samples=30)
    assert r["members"] == cortex.ENSEMBLE and r["arch"][1] == cortex.H1
    c = cortex.conviction({m: 0.5 for m in cortex.METHODS})
    assert 0.0 <= c["confidence"] <= 1.0
    assert abs(sum(c["saliency"].values()) - 1.0) < 1e-6      # normalized importances
    assert c["saliency"]["ta"] > 0 and c["saliency"]["ml"] > 0  # XOR voices carry weight


def test_nan_input_hardened(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    rng = np.random.default_rng(3)
    X = rng.normal(size=(120, len(cortex.METHODS)))
    y = (X[:, 0] > 0).astype(float)
    import trader.backprop as bp
    monkeypatch.setattr(bp, "build_dataset", lambda: (X, y))
    cortex.train(min_samples=30)
    c = cortex.conviction({"ta": float("nan"), "ml": float("inf"), "quant": 0.3})
    assert c["trained"] and -1.0 <= c["conviction"] <= 1.0     # no crash, finite output


def test_insufficient_data(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import trader.backprop as bp
    monkeypatch.setattr(bp, "build_dataset", lambda: (np.zeros((5, 7)), np.zeros(5)))
    assert cortex.train(min_samples=30)["trained"] is False
