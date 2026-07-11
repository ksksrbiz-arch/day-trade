"""Build a TensorTrade `TradingEnv` from a close-price series.

This is the bridge between the platform's data and TensorTrade's OMS. We keep
the honesty principle from `SimBroker` intact: every fill pays a commission
equal to the configured slippage haircut, so the RL agent is trained against
costs it will actually face -- it can't learn to churn a frictionless tape.

Design choices that matter:
  * Observations are PRE-COMPUTED NumPy feature columns (see features.py) fed as
    plain `Stream.source` nodes. TensorTrade's lazy `.diff()/.fillna()` operators
    emit `None` on the first tick under gym-0.26/pandas-3, which crashes the feed;
    precomputing sidesteps that entirely and makes train/infer inputs identical.
  * Action scheme is `BSH` (Buy/Sell/Hold) -> a clean 2-action space
    {0: flat/cash, 1: long/asset}. That maps directly onto the platform's
    long-biased paper trading (shorting stays off, matching ALLOW_SHORT default).
  * Reward is `SimpleProfit` (net-worth ratio), which avoids the buggy PBR diff.

All TensorTrade imports are function-local so the lean core never pays the
TensorFlow import cost unless RL is actually used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import build_features


@dataclass
class EnvConfig:
    window_size: int = 20
    slippage_bps: float = 10.0     # commission per fill, mirrors SimConfig honesty
    starting_cash: float = 10_000.0
    reward_scheme: str = "simple"  # SimpleProfit
    max_allowed_loss: float = 0.5  # episode ends if net worth drops below this frac


def build_env(closes, cfg: EnvConfig | None = None):
    """Return a ready-to-step TensorTrade `TradingEnv` over `closes`.

    Raises ImportError (with an install hint) if TensorTrade isn't available, and
    ValueError if the price history is too short for the requested window.
    """
    cfg = cfg or EnvConfig()
    try:
        from tensortrade.env.default import create
        from tensortrade.env.default.actions import BSH
        from tensortrade.feed.core import Stream, DataFeed
        from tensortrade.oms.exchanges import Exchange, ExchangeOptions
        from tensortrade.oms.services.execution.simulated import execute_order
        from tensortrade.oms.instruments import USD, Instrument
        from tensortrade.oms.wallets import Wallet, Portfolio
    except Exception as e:  # noqa: BLE001
        raise ImportError(
            "TensorTrade is not installed. Install the RL extra:\n"
            "  pip install --no-build-isolation -r requirements-rl.txt"
        ) from e

    prices = np.asarray([float(x) for x in closes], dtype=float)
    if len(prices) < cfg.window_size + 2:
        raise ValueError(
            f"need >= window_size+2 ({cfg.window_size + 2}) closes, got {len(prices)}"
        )
    feats, names = build_features(prices)

    RLA = Instrument("RLA", 2, "RL Asset")
    price_stream = Stream.source(prices.tolist(), dtype="float").rename("USD-RLA")
    commission = max(0.0, cfg.slippage_bps) / 10_000.0
    exchange = Exchange(
        "sim", service=execute_order, options=ExchangeOptions(commission=commission)
    )(price_stream)

    cash = Wallet(exchange, cfg.starting_cash * USD)
    asset = Wallet(exchange, 0 * RLA)
    portfolio = Portfolio(USD, [cash, asset])

    feed = DataFeed([
        Stream.source(feats[:, i].tolist(), dtype="float").rename(names[i])
        for i in range(feats.shape[1])
    ])

    action_scheme = BSH(cash=cash, asset=asset)
    env = create(
        portfolio=portfolio,
        action_scheme=action_scheme,
        reward_scheme=cfg.reward_scheme,
        feed=feed,
        window_size=cfg.window_size,
        max_allowed_loss=cfg.max_allowed_loss,
    )
    # stash for scoring/inspection by callers (backtest, cli)
    env._rl_portfolio = portfolio
    env._rl_prices = prices
    env._rl_feature_names = names
    return env


def buy_and_hold_return(closes) -> float:
    """Benchmark: fractional return of holding the asset over the window."""
    p = np.asarray([float(x) for x in closes], dtype=float)
    if len(p) < 2 or p[0] <= 0:
        return 0.0
    return float(p[-1] / p[0] - 1.0)
