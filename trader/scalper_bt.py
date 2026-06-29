"""
Backtest for the mean-reversion SCALPER (the live crypto-247 / scalper strategy),
so the 24/7 autotuner can optimize its parameters out-of-sample.

Strategy (matches trader/scalper.py intent): long-only mean reversion.
  * enter long when close <= lower Bollinger band (window, k)
  * exit when close >= mid band (mean reached)  OR  stop hit  OR  trail hit
  * slippage haircut on entry and exit (so a flat round-trip loses money)

Everything pure -> walk-forward sweep picks (window,k) on TRAIN, scores on the
NEXT unseen TEST window, rolls forward, and selects params that are ROBUST
across folds (best median OOS), not the single in-sample-luckiest fit. That
robustness rule is the main defense against the overfitting that 24/7
re-optimization invites.
"""
from __future__ import annotations

from statistics import fmean, pstdev, median
import math

from .scalper import bollinger


def backtest(closes: list[float], window: int, k: float,
             stop: float = 0.08, slip_bps: float = 10.0) -> dict:
    """Long-only mean-reversion backtest on one close series. Returns metrics."""
    slip = slip_bps / 1e4
    rets: list[float] = []          # per-trade returns (net of slippage)
    in_pos = False
    entry = 0.0
    n = len(closes)
    for i in range(window, n):
        b = bollinger(closes[:i + 1], window, k)
        if b is None:
            continue
        px = closes[i]
        if not in_pos:
            if px <= b.lower and b.lower < b.mid:
                in_pos = True
                entry = px * (1 + slip)         # pay slippage on entry
        else:
            hit_mean = px >= b.mid
            hit_stop = px <= entry * (1 - stop)
            if hit_mean or hit_stop:
                exitpx = px * (1 - slip)         # pay slippage on exit
                rets.append(exitpx / entry - 1.0)
                in_pos = False
    if not rets:
        return {"trades": 0, "total": 0.0, "win_rate": 0.0, "avg": 0.0,
                "sharpe": 0.0, "expectancy": 0.0}
    wins = [r for r in rets if r > 0]
    total = 1.0
    for r in rets:
        total *= (1 + r)
    mean = fmean(rets)
    sd = pstdev(rets) if len(rets) >= 2 else 0.0
    sharpe = (mean / sd) * math.sqrt(len(rets)) if sd > 0 else 0.0
    return {"trades": len(rets), "total": round((total - 1) * 100, 2),
            "win_rate": round(100 * len(wins) / len(rets), 1),
            "avg": round(mean * 100, 3), "expectancy": round(mean * 100, 3),
            "sharpe": round(sharpe, 2)}


GRID_W = [10, 20, 30]
GRID_K = [1.5, 2.0, 2.5]


def walk_forward(closes, train=300, test=120, stop=0.08, slip_bps=10.0,
                 grid_w=None, grid_k=None) -> dict:
    """Pick (window,k) by TRAIN expectancy, score on the next TEST window, roll.
    Returns stitched OOS metrics + the params chosen per fold."""
    grid_w = grid_w or GRID_W
    grid_k = grid_k or GRID_K
    n = len(closes)
    oos = []
    folds = []
    start = train
    while start + test <= n:
        tr = closes[start - train:start]
        te = closes[start - 30:start + test]   # carry a little lookback into test
        best, best_sc = None, -1e9
        for w in grid_w:
            for k in grid_k:
                m = backtest(tr, w, k, stop, slip_bps)
                sc = m["expectancy"] * math.log1p(max(0, m["trades"]))  # reward edge*activity
                if sc > best_sc:
                    best_sc, best = sc, (w, k)
        mt = backtest(te, best[0], best[1], stop, slip_bps)
        oos.append(mt)
        folds.append({"window": best[0], "k": best[1], **mt})
        start += test
    if not oos:
        return {"oos_trades": 0, "oos_total": 0.0, "oos_expectancy": 0.0,
                "oos_winrate": 0.0, "folds": []}
    tot = 1.0
    for m in oos:
        tot *= (1 + m["total"] / 100)
    return {"oos_trades": sum(m["trades"] for m in oos),
            "oos_total": round((tot - 1) * 100, 2),
            "oos_expectancy": round(median([m["expectancy"] for m in oos]), 3),
            "oos_winrate": round(fmean([m["win_rate"] for m in oos]), 1),
            "folds": folds}


def best_robust_params(panel_prices: dict, train=300, test=120, stop=0.08,
                       slip_bps=10.0) -> dict:
    """Across a universe, find the (window,k) that is robustly best OOS.
    We score each fixed (window,k) by its MEDIAN walk-forward OOS expectancy
    across all symbols -- robustness over peak. Returns {window,k,score,detail}."""
    best = None
    detail = {}
    for w in GRID_W:
        for k in GRID_K:
            exps = []
            for sym, closes in panel_prices.items():
                if len(closes) < train + test:
                    continue
                # fixed params (no per-fold re-pick) -> measures this config's own robustness
                wf = walk_forward(closes, train, test, stop, slip_bps, [w], [k])
                if wf["oos_trades"] > 0:
                    exps.append(wf["oos_expectancy"])
            if not exps:
                continue
            score = round(median(exps), 4)
            detail[f"w{w}_k{k}"] = {"median_oos_expectancy": score, "symbols": len(exps)}
            if best is None or score > best["score"]:
                best = {"window": w, "k": k, "score": score, "n_symbols": len(exps)}
    return {"best": best, "detail": detail}
