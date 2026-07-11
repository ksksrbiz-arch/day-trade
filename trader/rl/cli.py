"""CLI to train and backtest the RL trader.

Usage (needs the RL extra + Alpaca keys for live data; falls back to a passed file):

    # train a DQN for one or more symbols on recent Alpaca history
    python -m trader.rl.cli train AAPL MSFT --episodes 12 --lookback 400

    # greedy backtest of a saved model vs buy-and-hold
    python -m trader.rl.cli backtest AAPL --lookback 400

    # train/backtest from a local newline- or comma-separated close file (no keys)
    python -m trader.rl.cli train DEMO --closes data/demo_closes.txt --episodes 8

Honest-measurement note: the env charges a slippage commission on every fill
(SLIPPAGE_BPS), and backtest reports agent return vs buy-and-hold. If the agent
can't beat buy-and-hold over the window, the policy has no edge -- same bar the
rest of this platform holds itself to.
"""
from __future__ import annotations

import argparse
import sys

from .. import config
from .trader import RLTrader


def _closes_from_file(path: str) -> list[float]:
    raw = open(path).read().replace(",", "\n").split()
    return [float(x) for x in raw if x.strip()]


def _closes_from_market(symbol: str, lookback: int) -> list[float]:
    cfg = config.load()
    from ..marketdata import MarketData
    from ..massive import MassiveClient
    massive = MassiveClient(cfg.massive_access, cfg.massive_secret,
                            cfg.massive_endpoint, cfg.massive_bucket)
    md = MarketData(cfg.alpaca_key, cfg.alpaca_secret, massive=massive)
    return md.recent_closes(symbol, lookback_days=lookback)


def _get_closes(symbol: str, args) -> list[float]:
    if args.closes:
        return _closes_from_file(args.closes)
    return _closes_from_market(symbol, args.lookback)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="trader.rl.cli", description="TensorTrade RL trader")
    p.add_argument("command", choices=["train", "backtest"])
    p.add_argument("symbols", nargs="+", help="ticker(s), e.g. AAPL")
    p.add_argument("--episodes", type=int, default=8)
    p.add_argument("--lookback", type=int, default=400, help="days of history to pull")
    p.add_argument("--window", type=int, default=None, help="obs window (default from RL_WINDOW)")
    p.add_argument("--closes", default=None, help="local close-price file (skip market data)")
    p.add_argument("--slippage-bps", type=float, default=None)
    args = p.parse_args(argv)

    cfg = config.load()
    window = args.window if args.window is not None else cfg.strategy.rl_window
    slippage = args.slippage_bps if args.slippage_bps is not None else cfg.sim.slippage_bps
    rt = RLTrader(window=window, slippage_bps=slippage, model_dir=cfg.strategy.rl_model_dir or None)

    rc = 0
    for symbol in args.symbols:
        try:
            closes = _get_closes(symbol, args)
        except Exception as e:  # noqa: BLE001
            print(f"[{symbol}] data error: {e}"); rc = 1; continue
        if len(closes) < window + 2:
            print(f"[{symbol}] too few closes ({len(closes)}) for window {window}"); rc = 1; continue

        if args.command == "train":
            print(f"[{symbol}] training {args.episodes} episodes on {len(closes)} bars "
                  f"(window={window}, slippage={slippage}bps)")
            path = rt.train(symbol, closes, episodes=args.episodes)
            print(f"[{symbol}] saved -> {path}.keras")
            res = rt.backtest(symbol, closes)
            _report(res)
        else:
            res = rt.backtest(symbol, closes)
            _report(res)
    return rc


def _report(res) -> None:
    verdict = "BEATS" if res.beat_benchmark else "loses to"
    print(f"[{res.symbol}] agent {res.agent_return:+.2%} vs buy-and-hold "
          f"{res.benchmark_return:+.2%} over {res.n_steps} steps  ->  {verdict} benchmark "
          f"(final net worth {res.final_net_worth:,.2f})")


if __name__ == "__main__":
    sys.exit(main())
