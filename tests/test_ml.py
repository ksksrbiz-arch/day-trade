"""Deterministic tests for the ML layer (features, model, metrics)."""
import numpy as np

from trader.ml.features import feature_vector, FEATURES
from trader.ml.model import LogisticModel, auc, accuracy


def test_feature_vector_shape_and_range():
    up = [100 * (1.01 ** i) for i in range(80)]
    v, names = feature_vector(up)
    assert names == FEATURES and len(v) == len(FEATURES)
    assert all(-1.0001 <= x <= 1.0001 for x in v)


def test_feature_vector_too_short():
    v, names = feature_vector([1, 2, 3])
    assert v is None and names == FEATURES


def test_feature_determinism():
    s = [100 + (i % 7) - 3 + 0.1 * i for i in range(90)]
    assert feature_vector(s)[0] == feature_vector(s)[0]


def test_model_learns_separable_signal():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(600, 4))
    y = (X[:, 0] - X[:, 2] + rng.normal(scale=0.4, size=600) > 0).astype(int)
    m = LogisticModel(["a", "b", "c", "d"]).fit(X, y)
    p = m.proba(X)
    assert auc(y, p) > 0.85
    imp = m.importances()
    # the informative features should outrank the noise feature 'b'
    assert imp["a"] > imp["b"] and imp["c"] > imp["b"]


def test_score_one_in_range():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(300, 4)); y = (X[:, 0] > 0).astype(int)
    m = LogisticModel(["a", "b", "c", "d"]).fit(X, y)
    s = m.score_one([2, 0, 0, 0])
    assert -1.0 <= s <= 1.0 and s > 0          # strong positive feature -> bullish


def test_save_load_roundtrip(tmp_path):
    rng = np.random.default_rng(3)
    X = rng.normal(size=(200, 3)); y = (X[:, 1] > 0).astype(int)
    m = LogisticModel(["x", "y", "z"]).fit(X, y)
    m.meta = {"auc": 0.7}
    p = tmp_path / "m.json"; m.save(str(p))
    m2 = LogisticModel.load(str(p))
    assert m2.meta["auc"] == 0.7
    assert np.allclose(m.proba(X), m2.proba(X))


def test_auc_extremes():
    assert auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert auc([0, 1], [0.5, 0.5]) == 0.5
