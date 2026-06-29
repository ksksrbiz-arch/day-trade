"""Edge hunt: test concrete, well-known hypotheses on real history and keep only
what beats buy-and-hold -- including the user's thesis that the system should
SHORT the drop and profit when the market falls.

Strategies (all pure, all charged 5 bps per position change):
  * buy_hold            -- baseline
  * trend_long          -- long when price > SMA(n), else cash  (drawdown control)
  * trend_longshort     -- long above SMA(n), SHORT below        (profit on drops)
  * dual_momentum       -- hold the strongest of several assets; cash if all weak

The point isn't to win every line -- it's to MEASURE which behaviours actually
help, net of costs, over the available window.
"""
from __future__ import annotations

import math
from statistics import fmean, pstdev


def _metrics(rets: list[float]) -> dict:
    if not rets:
        return {"total": 0.0, "ann": 0.0, "sharpe": 0.0, "maxdd": 0.0, "days": 0}
    eq, curve = 1.0, []
    for r in rets:
        eq *= (1 + r); curve.append(eq)
    mean = fmean(rets); sd = pstdev(rets) or 1e-9
    peak, mdd = -1e9, 0.0
    for x in curve:
        peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    return {"total": round((eq - 1) * 100, 2),
            "ann": round(((1 + mean) ** 252 - 1) * 100, 2),
            "sharpe": round(mean / sd * math.sqrt(252), 2),
            "maxdd": round(mdd * 100, 2), "days": len(rets)}


def _rets(px: list[float]) -> list[float]:
    return [px[i] / px[i - 1] - 1 for i in range(1, len(px)) if px[i - 1]]


def _sma(px, i, n):
    if i < n:
        return None
    return fmean(px[i - n:i])


def buy_hold(px):
    return _rets(px)


def trend(px, n=200, allow_short=False, slip=0.0005):
    """Position set by yesterday's close vs SMA(n); applied to today's return."""
    rets, pos = [], 0
    for i in range(1, len(px)):
        sma = _sma(px, i, n)              # uses data up to i-1 (no lookahead)
        if sma is None:
            rets.append(0.0); continue
        want = 1 if px[i - 1] > sma else (-1 if allow_short else 0)
        cost = slip * abs(want - pos)
        pos = want
        r = (px[i] / px[i - 1] - 1) if px[i - 1] else 0.0
        rets.append(pos * r - cost)
    return rets


def dual_momentum(panel: dict, lookback=120, rebalance=21, slip=0.0005):
    """Each rebalance, hold the asset with the best trailing return; if the best
    is negative, go to cash. Classic absolute+relative momentum."""
    syms = list(panel)
    n = min(len(panel[s]) for s in syms)
    px = {s: panel[s][-n:] for s in syms}
    rets, held = [], None
    for i in range(1, n):
        if i % rebalance == 1 or held is None:
            best, bestret = None, 0.0
            for s in syms:
                if i > lookback and px[s][i - 1 - lookback]:
                    rr = px[s][i - 1] / px[s][i - 1 - lookback] - 1
                    if rr > bestret:
                        bestret, best = rr, s
            new = best  # None => cash
            cost = slip if new != held else 0.0
            held = new
        else:
            cost = 0.0
        if held:
            r = px[held][i] / px[held][i - 1] - 1 if px[held][i - 1] else 0.0
            rets.append(r - cost)
        else:
            rets.append(-cost)
    return rets


def run_hunt(verbose=True) -> dict:
    from .crsp import query as crsp
    from . import history

    def series(sym, start="2015-01-01"):
        bars = crsp.get_prices(sym, start, None)
        return [b["close"] for b in bars if b.get("close")]

    spy = series("SPY"); qqq = series("QQQ"); tlt = series("TLT"); gld = series("GLD")
    out = {}
    out["SPY buy&hold"] = _metrics(buy_hold(spy))
    out["SPY trend long-only (200d)"] = _metrics(trend(spy, 200, allow_short=False))
    out["SPY trend long/SHORT (200d)"] = _metrics(trend(spy, 200, allow_short=True))
    out["SPY trend long/SHORT (100d)"] = _metrics(trend(spy, 100, allow_short=True))
    dm = {"SPY": spy, "QQQ": qqq, "TLT": tlt, "GLD": gld}
    out["Dual momentum SPY/QQQ/TLT/GLD"] = _metrics(dual_momentum(dm))

    # crypto: BTC short-the-drop via CoinEx
    try:
        cp = history.load_panel(["BTC/USD"], days=1000, source="coinex")
        btc = cp["prices"].get("BTC/USD", [])
        if len(btc) > 220:
            out["BTC buy&hold"] = _metrics(buy_hold(btc))
            out["BTC trend long/SHORT (100d)"] = _metrics(trend(btc, 100, allow_short=True))
    except Exception as e:  # noqa: BLE001
        out["btc_error"] = str(e)[:100]

    base = out["SPY buy&hold"]["total"]
    ranked = sorted(((k, v) for k, v in out.items() if isinstance(v, dict)),
                    key=lambda kv: kv[1].get("sharpe", -9), reverse=True)
    if verbose:
        print(f"{'strategy':32s} {'total%':>9} {'ann%':>7} {'sharpe':>7} {'maxDD%':>8} {'beatsSPY':>8}")
        for k, v in ranked:
            beats = "YES" if v["total"] > base else "no"
            print(f"{k:32s} {v['total']:9.2f} {v['ann']:7.2f} {v['sharpe']:7.2f} {v['maxdd']:8.2f} {beats:>8}")
    return {"results": out, "ranked": [k for k, _ in ranked],
            "spy_total": base, "window_days": base and out["SPY buy&hold"]["days"]}


if __name__ == "__main__":
    run_hunt()
