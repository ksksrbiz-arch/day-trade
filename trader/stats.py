"""
Statistical-significance toolkit for net-of-fee edge.

Adapted from the fee-aware backtest in Krypt Trader
(https://github.com/scripflipped/Krypt-Trader, MIT License) -- the useful idea
there is not the Kalshi binary-contract math but the DISCIPLINE: never trust a
point estimate of "edge" without a standard error and a t-stat, and always net
out fees before deciding anything. That rigor is exactly what a thin-edge
equity/crypto book needs.

This version is market-agnostic: it operates on a list of per-trade NET returns
(fraction of stake, fees already subtracted). A round-trip fee model in basis
points replaces Kalshi's p*(1-p) contract fee. Pure stdlib.

Core question it answers: "is this edge distinguishable from zero, or is it
noise?" -- reported as mean, standard error, and t = mean/SE. |t| > ~2 ~= 95%
confidence the edge is real.
"""
from __future__ import annotations

import math

DEFAULT_FEE_BPS = 2.0            # round-trip cost assumption (commission+slippage), 2 bps


def net_return(entry: float, exit_: float, side: str = "buy",
               fee_bps: float = DEFAULT_FEE_BPS) -> float:
    """Per-trade net return as a fraction of stake, fees subtracted.

    Equity/crypto analogue of Krypt's net_pnl_per_contract: gross directional
    return minus a round-trip fee in basis points."""
    if not entry or entry <= 0 or exit_ is None:
        return 0.0
    gross = (exit_ / entry - 1.0) if side == "buy" else (entry / exit_ - 1.0)
    return gross - (fee_bps / 10_000.0)


def summarize(returns: list[float]) -> dict:
    """EV / standard error / t-stat over a list of per-trade net returns.

    Mirrors Krypt's summarize(): the value is the (net_ev, se, t) triple -- the
    t tells you whether the mean net return is statistically separable from 0."""
    n = len(returns)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "net_ev": 0.0, "se": 0.0,
                "t": 0.0, "total_net": 0.0, "sharpe": 0.0, "significant": False}
    wins = sum(1 for r in returns if r > 0)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    se = sd / math.sqrt(n) if n > 0 else 0.0
    t = (mean / se) if se > 0 else 0.0
    return {
        "n": n, "wins": wins, "win_rate": round(wins / n, 4),
        "net_ev": round(mean, 6), "se": round(se, 6), "t": round(t, 3),
        "total_net": round(sum(returns), 4),
        "sharpe": round(mean / sd, 4) if sd > 0 else 0.0,   # per-trade Sharpe
        "significant": bool(n >= 30 and mean > 0 and t > 2.0),
    }


def fade_summary(returns: list[float]) -> dict:
    """What if we bet the OPPOSITE of every signal? (Krypt's summarize_fade.)
    A signal with a significantly *negative* edge is a tradable *fade*."""
    return summarize([-r for r in returns])


def threshold_sweep(rows: list[dict], thresholds: list[float],
                    conf_key: str = "confidence", ret_key: str = "net") -> list[dict]:
    """For each confidence threshold, significance of the surviving subset.
    Surfaces "the edge only exists when filtered to confidence >= X" (Krypt)."""
    out = []
    for th in thresholds:
        sub = [r[ret_key] for r in rows if float(r.get(conf_key, 0.0)) >= th]
        out.append({"threshold": th, **summarize(sub)})
    return out


def verdict(overall: dict, sweep: list[dict] | None = None) -> str:
    """Plain-English significance verdict (adapted from Krypt's verdict())."""
    if overall["n"] < 30:
        return (f"INCONCLUSIVE - only {overall['n']} trades (need ~100+). "
                "Not enough data to separate edge from noise.")
    if overall["net_ev"] > 0 and overall["t"] > 2:
        return (f"POSITIVE net-of-fee edge and statistically significant "
                f"(net {overall['net_ev']:+.4f}/trade, t={overall['t']:+.1f}). Worth hardening.")
    best = None
    if sweep:
        best = max((r for r in sweep if r["n"] >= 30), key=lambda r: r["net_ev"], default=None)
    if best and best["net_ev"] > 0 and best["t"] > 2:
        return (f"Edge appears only when filtered to confidence >= {best['threshold']:.2f} "
                f"(net {best['net_ev']:+.4f}/trade, t={best['t']:+.1f}). Tighten the gate.")
    if overall["net_ev"] > 0:
        return (f"Marginally positive but WITHIN NOISE (t={overall['t']:+.1f} < 2). "
                "Not yet distinguishable from zero -- need more data or a real edge.")
    fade = fade_summary_from_overall(overall)
    tail = ""
    if fade and fade > 2:
        tail = f" The INVERSE is significant (fade t={fade:+.1f}) -- consider fading it."
    return (f"NEGATIVE after fees (net {overall['net_ev']:+.4f}/trade). "
            f"No net-of-fee edge as configured.{tail}")


def fade_summary_from_overall(overall: dict) -> float:
    """t-stat of the fade (opposite sign, same magnitude) from an overall dict."""
    return -overall.get("t", 0.0)


if __name__ == "__main__":
    import random
    random.seed(1)
    # a weak-but-real +5bps/trade edge buried in 1% noise
    rs = [0.0005 + random.gauss(0, 0.01) for _ in range(300)]
    s = summarize(rs)
    print(s)
    print(verdict(s))
