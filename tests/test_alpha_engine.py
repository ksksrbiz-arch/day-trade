"""Tests for the alpha_engine voice (prediction-alpha-engine service adapter)."""
import numpy as np

from trader import alpha, alpha_engine, backprop, cortex, voices


def _reset(monkeypatch):
    alpha_engine._cache.clear()
    monkeypatch.delenv("USE_ALPHA_ENGINE", raising=False)


# ---- adapter ---------------------------------------------------------------- #
def test_score_signal_parses_and_clamps(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(alpha_engine, "_get",
                        lambda path, params=None: {"symbol": "SPY", "score": -1.7, "n": 2})
    assert alpha_engine.score_signal("spy") == -1.0          # clamped to [-1,1]


def test_score_signal_abstains_and_negative_caches(monkeypatch):
    _reset(monkeypatch)
    calls = {"n": 0}

    def _boom(path, params=None):
        calls["n"] += 1
        raise ConnectionError("service down")

    monkeypatch.setattr(alpha_engine, "_get", _boom)
    assert alpha_engine.score_signal("SPY") is None
    assert alpha_engine.score_signal("SPY") is None          # served from negative cache
    assert calls["n"] == 1


def test_null_score_means_abstain(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(alpha_engine, "_get",
                        lambda path, params=None: {"symbol": "SPY", "score": None, "n": 0})
    assert alpha_engine.score_signal("SPY") is None


def test_disabled_via_env(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("USE_ALPHA_ENGINE", "false")
    monkeypatch.setattr(alpha_engine, "_get",
                        lambda path, params=None: (_ for _ in ()).throw(AssertionError("must not call")))
    assert alpha_engine.score_signal("SPY") is None
    assert alpha_engine.signals(["SPY"]) == []
    assert alpha_engine.status()["enabled"] is False


def test_status_reports_reachability(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(alpha_engine, "_get",
                        lambda path, params=None: {"status": "ok", "cached_opportunities": 42})
    s = alpha_engine.status()
    assert s["reachable"] is True and s["cached_opportunities"] == 42


# ---- voice registration ------------------------------------------------------ #
def test_registered_everywhere():
    assert "alpha_engine" in cortex.METHODS
    assert "alpha_engine" in backprop.METHODS
    assert "alpha_engine" in voices.METHODS
    assert "alpha_engine" in alpha._BASE_W
    for rw in alpha._REGIME_W.values():
        assert "alpha_engine" in rw


def test_confluence_blends_alpha_engine_vote():
    conv = alpha.confluence(ta=0.6, quant=0.5, alpha_engine=0.7, regime="risk_off")
    assert conv.scores.get("alpha_engine") == 0.7
    assert conv.agree == 3 and conv.side == "buy"
    # and it can abstain without changing method count
    conv2 = alpha.confluence(ta=0.6, quant=0.5, alpha_engine=None)
    assert "alpha_engine" not in conv2.scores


def test_analyze_calls_the_voice(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(alpha_engine, "score_signal", lambda s: 0.5)
    closes = [100 + i * 0.5 for i in range(80)]
    conv = alpha.analyze(closes, symbol="SPY", use_ml=False, use_prediction=False,
                         use_tnet=False, use_cortex=False)
    assert conv.scores.get("alpha_engine") == 0.5


# ---- cortex arch guard -------------------------------------------------------- #
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex, "WEIGHTS", str(tmp_path / "c.npz"))
    monkeypatch.setattr(cortex, "CARD", str(tmp_path / "card.json"))
    monkeypatch.setattr(cortex, "_DATA", str(tmp_path))
    cortex._cache.update(ts=0.0, params=None, loaded=False)


def test_cortex_stale_arch_abstains(tmp_path, monkeypatch):
    """Weights trained before alpha_engine existed (7 inputs) must abstain, not crash."""
    _iso(tmp_path, monkeypatch)
    old_d = len(cortex.METHODS) - 1
    members = [cortex._init(old_d, cortex.H1, cortex.H2, s) for s in cortex._SEEDS]
    cortex._save(members, {"trained": True, "arch": [old_d, cortex.H1, cortex.H2]})
    c = cortex.conviction({m: 0.5 for m in cortex.METHODS})
    assert c["trained"] is False and c.get("stale_arch") is True and c["conviction"] == 0.0


def test_cortex_retrain_promotes_over_stale_champion(tmp_path, monkeypatch):
    """A perfect old-arch champion must not block the first new-arch ensemble."""
    _iso(tmp_path, monkeypatch)
    old_d = len(cortex.METHODS) - 1
    members = [cortex._init(old_d, cortex.H1, cortex.H2, s) for s in cortex._SEEDS]
    cortex._save(members, {"trained": True, "val_acc": 0.99,
                           "arch": [old_d, cortex.H1, cortex.H2]})
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(cortex.METHODS)))
    y = (X[:, 0] + X[:, 1] > 0).astype(float)
    monkeypatch.setattr(backprop, "build_dataset", lambda: (X, y))
    r = cortex.train(min_samples=30)
    assert r["trained"] is True and r["promoted"] is True
    assert r["arch"][0] == len(cortex.METHODS)
    c = cortex.conviction({m: 0.5 for m in cortex.METHODS})
    assert c["trained"] is True                       # new-width weights serve again
