"""Tests for the TensorTrade RL trader.

The pure-NumPy feature tests always run (no heavy deps). The env/agent tests are
skipped automatically when the optional RL extra isn't installed, so the lean
core CI stays green without TensorFlow.
"""
import numpy as np
import pytest

from trader.rl import available, build_features, latest_window, FEATURE_NAMES
from trader.rl.env import buy_and_hold_return
from trader.rl.trader import model_path

rl_only = pytest.mark.skipif(not available(), reason="TensorTrade (RL extra) not installed")


# ---- pure feature tests (always run) ------------------------------------- #

def test_features_shape_and_names():
    closes = list(np.linspace(100, 120, 60))
    mat, names = build_features(closes)
    assert names == FEATURE_NAMES
    assert mat.shape == (60, len(FEATURE_NAMES))


def test_features_are_finite_and_causal():
    rng = np.random.RandomState(0)
    closes = (100 + np.cumsum(rng.normal(0, 1, 200))).tolist()
    mat, _ = build_features(closes)
    assert np.isfinite(mat).all()
    # recomputing on a prefix must match the prefix of the full matrix (no lookahead)
    pref, _ = build_features(closes[:120])
    assert np.allclose(pref, mat[:120], atol=1e-5)


def test_features_handle_bad_prints():
    closes = [100, 0, -5, 101, 102, 103, 104, 105]  # zero/negative should be healed
    mat, _ = build_features(closes)
    assert np.isfinite(mat).all()


def test_features_reject_too_short():
    with pytest.raises(ValueError):
        build_features([100.0])


def test_latest_window_pads_short_history():
    closes = [100, 101, 102, 103]
    win = latest_window(closes, window=10)
    assert win.shape == (10, len(FEATURE_NAMES))


def test_buy_and_hold_return():
    assert buy_and_hold_return([100, 110]) == pytest.approx(0.10)
    assert buy_and_hold_return([100]) == 0.0


def test_model_path_is_symbol_safe():
    p = model_path("BTC/USD", model_dir="/tmp/x")
    assert p.endswith("BTC_USD")


# ---- confluence-voice wiring (pure; no TF needed) ------------------------ #

def test_confluence_accepts_rl_voice():
    """The RL score participates as a first-class confluence method."""
    from trader.alpha import confluence
    conv = confluence(ta=0.5, quant=0.4, rl=0.6, regime="neutral",
                      min_agree=2, min_composite=0.10)
    assert "rl" in conv.scores
    assert conv.side == "buy"
    # a strong opposing RL vote drags the composite toward flat/sell
    opp = confluence(ta=0.5, quant=0.4, rl=-0.9, regime="neutral",
                     min_agree=2, min_composite=0.10)
    assert opp.composite < conv.composite


def test_confluence_rl_absent_by_default():
    """analyze() must not add an rl voice unless use_rl is set (back-compat)."""
    import numpy as np
    from trader.alpha import analyze
    closes = list(100 + np.cumsum(np.random.RandomState(4).normal(0, 1, 80)))
    conv = analyze(closes, symbol="ZZZ", regime="neutral", min_agree=1, min_composite=0.05)
    assert "rl" not in conv.scores


def test_score_from_closes_none_without_model(tmp_path):
    from trader.rl import score_from_closes
    import numpy as np
    closes = list(100 + np.cumsum(np.random.RandomState(6).normal(0, 1, 60)))
    # empty model dir -> no model -> voice absent (None), never raises
    assert score_from_closes("NOPE", closes, window=10, model_dir=str(tmp_path)) is None


# ---- env / agent tests (need the RL extra) ------------------------------- #

@rl_only
def test_build_env_steps():
    from trader.rl.env import build_env, EnvConfig
    from trader.rl.agent import _unpack_step, _unpack_reset
    rng = np.random.RandomState(1)
    closes = (100 + np.cumsum(rng.normal(0, 1, 120))).tolist()
    env = build_env(closes, EnvConfig(window_size=10, slippage_bps=10.0))
    assert env.action_space.n == 2  # BSH: flat / long
    obs = _unpack_reset(env.reset())
    assert np.shape(obs) == (10, len(FEATURE_NAMES))
    _, reward, done, _ = _unpack_step(env.step(1))
    assert isinstance(reward, float)


@rl_only
def test_env_rejects_short_history():
    from trader.rl.env import build_env, EnvConfig
    with pytest.raises(ValueError):
        build_env([100, 101, 102], EnvConfig(window_size=20))


@rl_only
def test_train_backtest_decide_roundtrip(tmp_path):
    from trader.rl import RLTrader
    from trader.strategy import StrategyConfig
    rng = np.random.RandomState(2)
    closes = (100 + np.cumsum(rng.normal(0.05, 1, 140))).tolist()
    rt = RLTrader(window=10, slippage_bps=10.0, model_dir=str(tmp_path))
    path = rt.train("TEST", closes, episodes=1, max_steps=120, warmup=32,
                    batch_size=16, verbose=False)
    import os
    assert os.path.exists(path + ".keras")
    # a fresh instance must be able to load the saved model and act
    rt2 = RLTrader(window=10, slippage_bps=10.0, model_dir=str(tmp_path))
    res = rt2.backtest("TEST", closes)
    assert res.symbol == "TEST"
    assert res.n_steps > 0
    pos = rt2.target_position("TEST", closes)
    assert pos in (0, 1)
    intent = rt2.decide("TEST", closes, StrategyConfig(), open_symbols={"TEST"})
    assert intent is None  # already open -> never re-enters


@rl_only
def test_score_from_closes_bounded_with_model(tmp_path):
    from trader.rl import RLTrader, score_from_closes
    rng = np.random.RandomState(9)
    closes = (100 + np.cumsum(rng.normal(0.05, 1, 140))).tolist()
    RLTrader(window=10, model_dir=str(tmp_path)).train(
        "VOX", closes, episodes=1, max_steps=120, warmup=32, batch_size=16, verbose=False)
    s = score_from_closes("VOX", closes, window=10, model_dir=str(tmp_path))
    assert s is not None and -1.0 <= s <= 1.0


@rl_only
def test_decide_returns_none_without_model(tmp_path):
    from trader.rl import RLTrader
    from trader.strategy import StrategyConfig
    rt = RLTrader(window=10, model_dir=str(tmp_path))  # empty dir, no model
    closes = list(np.linspace(100, 110, 40))
    assert rt.decide("NOPE", closes, StrategyConfig()) is None
