"""Reinforcement-learning trader built on TensorTrade (optional extra).

The heavy stack (TensorTrade + TensorFlow) is NOT a core dependency. Everything
here lazy-imports it, so `import trader.rl` is cheap and the platform boots even
when the RL extra isn't installed. Call `available()` to gate RL code paths.

Install the extra with:
    pip install --no-build-isolation -r requirements-rl.txt

Public surface:
    available()            -> bool: is TensorTrade importable?
    RLTrader               -> train / backtest / decide (the full RL trader)
    EnvConfig, build_env   -> construct a TensorTrade TradingEnv from closes
    build_features         -> pure-NumPy observation features (no TF needed)
"""
from __future__ import annotations

from .features import build_features, latest_window, FEATURE_NAMES
from .env import EnvConfig, build_env, buy_and_hold_return
from .trader import RLTrader, RLResult, model_path, score_from_closes

__all__ = [
    "available", "RLTrader", "RLResult", "model_path", "score_from_closes",
    "EnvConfig", "build_env", "buy_and_hold_return",
    "build_features", "latest_window", "FEATURE_NAMES",
]


def available() -> bool:
    """True when TensorTrade (and thus the RL stack) can be imported."""
    try:
        import tensortrade  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False
