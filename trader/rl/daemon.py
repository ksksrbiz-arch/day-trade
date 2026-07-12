"""Continuous-improvement daemon for the RL trader.

Mirrors `trader.ml.daemon`: retrain on a fixed cadence, but with a
**champion/challenger gate** so the live model only ever improves. On each cycle,
per symbol:

  1. Split recent history into a training slice and a HELD-OUT tail.
  2. Backtest the incumbent (champion) on the held-out tail -> champion return.
  3. Train a challenger on the training slice only, backtest it on the SAME
     held-out tail -> challenger return.
  4. Promote the challenger ONLY if it beats the champion out-of-sample (or there
     is no champion yet). Otherwise keep the incumbent untouched.

The held-out evaluation is the honesty layer: a challenger can't win by memorising
the very bars it's scored on. Same bar the rest of this platform holds itself to.

Run detached, or opt in via the container entrypoint (RUN_RL_DAEMON=1):

    python -m trader.rl.daemon --every 12 --episodes 12
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import os
import shutil
import sys
import time

from . import available
from .env import buy_and_hold_return
from .trader import RLTrader, model_path, DEFAULT_MODEL_DIR

_CORE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"]


def _eval_universe(cfg) -> list[str]:
    """RL_UNIVERSE if set, else the configured UNIVERSE, else a liquid basket."""
    syms = list(getattr(cfg.strategy, "rl_universe", ()) or [])
    if not syms:
        syms = list(getattr(cfg.strategy, "universe", set()) or [])
    out, seen = [], set()
    for s in (syms + _CORE):
        s = str(s).upper().strip()
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out[:16]


def _promote(challenger_dir: str, live_dir: str, symbol: str) -> None:
    """Atomically copy a challenger's model files over the live model."""
    src = model_path(symbol, challenger_dir)
    dst = model_path(symbol, live_dir)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    for ext in (".keras", ".meta.json"):
        if os.path.exists(src + ext):
            tmp = dst + ext + ".tmp"
            shutil.copy2(src + ext, tmp)
            os.replace(tmp, dst + ext)


def retrain_symbol(symbol: str, closes, model_dir: str, window: int,
                   slippage_bps: float, episodes: int, holdout: int | None = None) -> dict:
    """Champion/challenger retrain for one symbol on a close-price series."""
    holdout = holdout or max(window, 30)
    need = 2 * window + holdout + 10
    if len(closes) < need:
        return {"symbol": symbol, "skipped": f"thin history ({len(closes)}<{need})"}

    train_series = closes[:-holdout]
    eval_series = closes[-(window + holdout):]
    bench = buy_and_hold_return(eval_series)

    # champion: the incumbent, forward-tested on the held-out tail
    champ_ret = None
    try:
        live = RLTrader(window=window, slippage_bps=slippage_bps, model_dir=model_dir)
        if live._get_agent(symbol) is not None:
            champ_ret = live.backtest(symbol, eval_series).agent_return
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "error": f"champion eval: {str(e)[:80]}"}

    # challenger: trained on the training slice only, scored on the same tail
    chal_dir = os.path.join(model_dir, "_challenger")
    try:
        chal = RLTrader(window=window, slippage_bps=slippage_bps, model_dir=chal_dir)
        chal.train(symbol, train_series, episodes=episodes, verbose=False)
        chal_ret = chal.backtest(symbol, eval_series).agent_return
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "champion": champ_ret, "error": f"challenger: {str(e)[:80]}"}

    promote = champ_ret is None or chal_ret > champ_ret
    if promote:
        _promote(chal_dir, model_dir, symbol)
    return {"symbol": symbol, "champion": champ_ret, "challenger": round(chal_ret, 5),
            "benchmark": round(bench, 5), "promoted": bool(promote),
            "n_train": len(train_series), "n_eval": len(eval_series)}


def retrain_once(cfg=None, md=None, episodes: int = 12) -> list[dict]:
    """One full retrain sweep across the RL universe. Returns per-symbol results."""
    if not available():
        return [{"error": "TensorTrade not installed (requirements-rl.txt)"}]
    from .. import config as _config
    cfg = cfg or _config.load()
    window = cfg.strategy.rl_window
    slippage = cfg.sim.slippage_bps
    model_dir = cfg.strategy.rl_model_dir or DEFAULT_MODEL_DIR
    if md is None:
        from ..marketdata import MarketData
        from ..massive import MassiveClient
        massive = MassiveClient(cfg.massive_access, cfg.massive_secret,
                                cfg.massive_endpoint, cfg.massive_bucket)
        md = MarketData(cfg.alpaca_key, cfg.alpaca_secret, massive=massive)

    results = []
    for sym in _eval_universe(cfg):
        try:
            closes = md.recent_closes(sym, lookback_days=max(400, window * 12))
        except Exception as e:  # noqa: BLE001
            results.append({"symbol": sym, "error": f"data: {str(e)[:80]}"}); continue
        results.append(retrain_symbol(sym, closes, model_dir, window, slippage, episodes))
    return results


def main():
    every_h, episodes = 12.0, 12
    for i, a in enumerate(sys.argv):
        if a == "--every" and i + 1 < len(sys.argv):
            every_h = float(sys.argv[i + 1])
        if a == "--episodes" and i + 1 < len(sys.argv):
            episodes = int(sys.argv[i + 1])
    if not available():
        print("[rl.daemon] TensorTrade not installed -- run: "
              "pip install --no-build-isolation -r requirements-rl.txt")
        return
    print(f"[rl.daemon] retraining every {every_h}h, {episodes} episodes "
          f"(champion/challenger, out-of-sample gated)")
    while True:
        try:
            res = retrain_once(episodes=episodes)
            promoted = [r["symbol"] for r in res if r.get("promoted")]
            print(f"[rl.daemon] {time.strftime('%Y-%m-%d %H:%M')} swept {len(res)} "
                  f"-> promoted {len(promoted)}: {promoted}")
            for r in res:
                if r.get("error") or r.get("skipped"):
                    print(f"[rl.daemon]   {r.get('symbol','?')}: "
                          f"{r.get('error') or r.get('skipped')}")
        except Exception as e:  # noqa: BLE001
            print(f"[rl.daemon] sweep error: {e}")
        time.sleep(every_h * 3600)


if __name__ == "__main__":
    main()
