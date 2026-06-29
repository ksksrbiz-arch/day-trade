"""Tests for the temporal-transformer feature layer (pure-math, no network)."""
import numpy as np
from trader import tnet


def test_revin_reversible():
    x = np.array([10.0, 11, 9, 13, 12, 14])
    xn, st = tnet.revin(x)
    assert abs(xn.mean()) < 1e-6 and abs(xn.std() - 1) < 1e-3
    assert np.allclose(tnet.revin_inv(xn, st), x)


def test_sliding_dilated_mask():
    m = tnet.sliding_dilated_mask(20, window=4, dilation=2)
    assert m[19, 19] and m[19, 18] and m[19, 17] and m[19, 16]   # local window
    assert not m[0, 19]                                          # causal: no future
    assert m[19].sum() < 19                                      # sparse, not full


def test_patchify_shapes():
    p = tnet.patchify(np.arange(100.0), patch=16, stride=8)
    assert p.ndim == 2 and p.shape[1] == 16 and p.shape[0] > 1


def test_vsn_vol_gating():
    feats = {"price_mom": 0.5, "volume_z": 0.5}
    calm = tnet.vsn(feats, vol_z=0.0)
    spike = tnet.vsn(feats, vol_z=2.0)
    # in a vol spike, volume gains weight share and price loses it
    assert spike["volume_z"] > calm["volume_z"]
    assert spike["price_mom"] < calm["price_mom"]


def test_attention_softmax_normalized():
    rng = np.random.default_rng(0)
    Q = rng.normal(size=(5, 8)); K = rng.normal(size=(5, 8)); V = rng.normal(size=(5, 8))
    _, w = tnet.attention(Q, K, V)
    assert np.allclose(w.sum(axis=-1), 1.0)


def test_cross_attention_distribution():
    base = np.cumsum(np.random.default_rng(1).normal(size=200)) + 100
    factors = {"twin": base * 1.0 + 0.01, "noise": np.random.default_rng(2).normal(size=200) + 50}
    d = tnet.cross_attention(base, factors)
    assert abs(sum(d["weights"].values()) - 1.0) < 1e-6
    assert d["dominant"] == "twin"          # asset attends most to its correlated twin


def test_infonce_pulls_positive():
    a = np.array([1.0, 0, 0]); pos = np.array([0.9, 0.1, 0]); neg = np.array([[0, 1.0, 0], [0, 0, 1.0]])
    loss_good = tnet.info_nce_loss(a, pos, neg)
    loss_bad = tnet.info_nce_loss(a, np.array([0, 1.0, 0]), neg)
    assert loss_good < loss_bad             # aligned positive -> lower loss


def test_rope_shape_preserved():
    x = np.random.default_rng(3).normal(size=(10, 8))
    y = tnet.rope(x)
    assert y.shape == x.shape and not np.allclose(y, x)


def test_temporal_features_cyclic():
    from datetime import datetime
    f = tnet.temporal_features(datetime(2026, 6, 26, 14, 30))
    assert set(["min_sin", "hour_cos", "dow_sin"]).issubset(f)
    assert all(-1.0001 <= v <= 1.0001 for v in f.values())


# ---------------- expansion: math helpers ---------------- #
def test_softmax_layernorm_gelu():
    z = np.array([[1.0, 2, 3], [0, 0, 0]])
    s = tnet.softmax(z)
    assert np.allclose(s.sum(axis=-1), 1.0) and (s >= 0).all()
    ln = tnet.layernorm(np.array([[1.0, 2, 3, 4]]))
    assert abs(ln.mean()) < 1e-6 and abs(ln.std() - 1) < 1e-2
    # gelu(0)=0, ~identity for large x, monotone increasing on the positive arm
    assert abs(tnet.gelu(np.array([0.0]))[0]) < 1e-9
    assert abs(tnet.gelu(np.array([5.0]))[0] - 5.0) < 0.05
    gp = tnet.gelu(np.linspace(0.1, 3, 40))
    assert np.all(np.diff(gp) > 0)


# ---------------- expansion: multi-head attention ---------------- #
def test_multi_head_attention_normalized_and_masked():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(12, 16))
    mask = tnet.sliding_dilated_mask(12, window=4, dilation=2)
    out, w = tnet.multi_head_attention(X, n_heads=4, mask=mask)
    assert out.shape == (12, 16)
    assert np.allclose(w.sum(axis=-1), 1.0)        # rows normalized
    # causal: query 0 puts ~no weight on future keys
    assert w[0, 5:].sum() < 1e-6


def test_attention_entropy_bounds():
    n = 8
    uniform = np.ones((n, n)) / n
    onehot = np.eye(n)
    assert tnet.attention_entropy(uniform) > 0.95     # diffuse -> ~1
    assert tnet.attention_entropy(onehot) < 0.05      # focused -> ~0


# ---------------- expansion: composed encoder ---------------- #
def test_encoder_shapes_and_determinism():
    x = np.cumsum(np.random.default_rng(1).normal(size=300)) + 100
    a = tnet.encode(x)
    b = tnet.encode(x)
    assert a["pooled"].shape == (32,)
    assert a["n_patches"] > 1
    assert len(a["entropy"]) == 2                      # n_layers
    assert np.allclose(a["pooled"], b["pooled"])       # deterministic
    assert np.isfinite(a["pooled"]).all()


def test_encoder_short_series_failsoft():
    out = tnet.encode(np.arange(5.0))
    assert out["n_patches"] == 0 and out["pooled"].shape == (32,)


# ---------------- expansion: Platt calibration ---------------- #
def test_platt_fit_separable():
    rng = np.random.default_rng(2)
    scores = np.concatenate([rng.normal(0.6, 0.2, 60), rng.normal(-0.6, 0.2, 60)])
    labels = np.concatenate([np.ones(60), np.zeros(60)])
    t, b = tnet._fit_platt(scores, labels)
    p = tnet.sigmoid(t * scores + b)
    acc = np.mean((p >= 0.5) == (labels >= 0.5))
    assert t > 0 and acc > 0.85                        # learns correct mapping


def test_directional_readout_trend_sign():
    up = np.linspace(100, 130, 120) + np.random.default_rng(3).normal(0, 0.2, 120)
    down = np.linspace(130, 100, 120) + np.random.default_rng(4).normal(0, 0.2, 120)
    ru = tnet.directional_readout(up)
    rd = tnet.directional_readout(down)
    assert ru["raw"] > 0 and rd["raw"] < 0
    assert -1 <= ru["raw"] <= 1 and -1 <= rd["raw"] <= 1


def test_revin_hardened_finite():
    xn, st = tnet.revin(np.array([1.0, np.nan, np.inf, -np.inf, 2.0]))
    assert np.all(np.isfinite(xn))                 # NaN/inf scrubbed
    assert tnet.revin(np.array([]))[1] == (0.0, 1.0)   # empty -> unit-scale fallback


def test_encode_robust_on_degenerate_series():
    e1 = tnet.encode(np.ones(300))                 # constant series
    assert np.all(np.isfinite(e1["pooled"])) and e1["pooled"].shape == (32,)
    e2 = tnet.encode(np.arange(5.0))               # shorter than a patch
    assert e2["pooled"].shape == (32,) and e2["n_patches"] == 0


def test_directional_readout_constant_series():
    r = tnet.directional_readout(np.ones(60))
    assert abs(r["raw"]) < 1e-6 and np.isfinite(r["vol"])


def test_prune_logs_dedup_and_age(tmp_path, monkeypatch):
    import json, time
    flog = tmp_path / "f.jsonl"
    now = time.time()
    rows = [
        {"ts": now - 1, "symbol": "SPY", "raw": 0.1, "ref": 100},      # same-day SPY (older)
        {"ts": now, "symbol": "SPY", "raw": 0.2, "ref": 101},          # same-day SPY (latest -> kept)
        {"ts": now, "symbol": "AAPL", "raw": 0.3, "ref": 50},          # kept
        {"ts": now - 100 * 86400, "symbol": "OLD", "raw": 0.4, "ref": 10},  # too old -> dropped
    ]
    flog.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(tnet, "_FLOG", str(flog))
    kept = tnet.prune_logs(max_age_days=60)
    assert kept == 2
    out = [json.loads(l) for l in open(flog) if l.strip()]
    assert {r["symbol"] for r in out} == {"SPY", "AAPL"}
    spy = [r for r in out if r["symbol"] == "SPY"][0]
    assert spy["raw"] == 0.2                                            # latest per day wins


def test_quantiles_ordered_and_prob_range():
    # build a forecast directly from a synthetic readout path via _prob_up
    p, _ = tnet._prob_up(0.5)
    assert 0.0 <= p <= 1.0
    p_lo, _ = tnet._prob_up(-2.0)
    p_hi, _ = tnet._prob_up(2.0)
    assert p_lo < 0.5 < p_hi                            # monotone in raw
