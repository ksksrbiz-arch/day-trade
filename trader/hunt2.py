"""Batch-2 edge hunt: date-aligned multi-asset strategies on real history.

Adds defensive rotation, long-only cross-sectional momentum, vol-targeted
trend, and crypto trend/rotation -- and keeps only what beats buy-and-hold.
Reuses hunt._metrics / hunt.trend so results are comparable.
"""
from __future__ import annotations

from statistics import fmean, pstdev

from .hunt import _metrics, trend, buy_hold


def _aligned(symbols, start="2015-01-01", source="crsp"):
    """Return {sym: closes} aligned on common dates (offline CRSP cache)."""
    from .crsp import query as crsp
    per = {}
    for s in symbols:
        bars = crsp.get_prices(s, start, None)
        per[s] = {b["date"]: b["close"] for b in bars if b.get("close")}
    common = None
    for s in symbols:
        ds = set(per[s])
        common = ds if common is None else (common & ds)
    common = sorted(common or [])
    return {s: [per[s][d] for d in common] for s in symbols}, common


def defensive_rotation(risk_sym, safe_sym, lookback=120, rebalance=21, slip=0.0005):
    """Hold the risk asset when its trailing return beats the safe asset, else
    rotate to the safe asset (classic SPY/TLT defensive momentum)."""
    al, _ = _aligned([risk_sym, safe_sym])
    n = min(len(al[risk_sym]), len(al[safe_sym]))
    r, s = al[risk_sym][-n:], al[safe_sym][-n:]
    rets, held = [], None
    for i in range(1, n):
        if i % rebalance == 1 or held is None:
            if i > lookback and r[i - 1 - lookback] and s[i - 1 - lookback]:
                rr = r[i - 1] / r[i - 1 - lookback] - 1
                sr = s[i - 1] / s[i - 1 - lookback] - 1
                new = risk_sym if rr >= sr else safe_sym
            else:
                new = risk_sym
            cost = slip if new != held else 0.0
            held = new
        else:
            cost = 0.0
        px = al[held]
        rets.append((px[i] / px[i - 1] - 1 if px[i - 1] else 0.0) - cost)
    return rets


def xs_momentum_long(symbols, lookback=120, top_n=5, rebalance=21, slip=0.0005):
    """Long-only cross-sectional momentum: equal-weight the top_n trailing
    performers, rebalanced periodically (no shorting -- the variant that the
    batch-1 hunt suggested might work where long/short failed)."""
    al, common = _aligned(symbols)
    n = len(common)
    if n < lookback + 30:
        return []
    rets, weights = [], {}
    for i in range(1, n):
        if i % rebalance == 1 or not weights:
            scores = {}
            for s in symbols:
                px = al[s]
                if i > lookback and px[i - 1 - lookback]:
                    scores[s] = px[i - 1] / px[i - 1 - lookback] - 1
            top = sorted(scores, key=scores.get, reverse=True)[:top_n]
            new = {s: 1.0 / len(top) for s in top} if top else {}
            turn = sum(abs(new.get(s, 0) - weights.get(s, 0))
                       for s in set(new) | set(weights))
            cost = slip * turn
            weights = new
        else:
            cost = 0.0
        rp = 0.0
        for s, w in weights.items():
            px = al[s]
            if px[i - 1]:
                rp += w * (px[i] / px[i - 1] - 1)
        rets.append(rp - cost)
    return rets


def vol_target_trend(px, n=200, target=0.12, slip=0.0005):
    """Trend-timing long, position scaled to a target annualized vol."""
    rets, pos = [], 0.0
    for i in range(1, len(px)):
        if i < n:
            rets.append(0.0); continue
        sma = fmean(px[i - n:i])
        win = [px[j] / px[j - 1] - 1 for j in range(i - 20, i) if px[j - 1]]
        vol = (pstdev(win) * (252 ** 0.5)) if len(win) > 2 else 0.2
        want = (min(1.5, target / vol) if px[i - 1] > sma else 0.0)
        cost = slip * abs(want - pos); pos = want
        rets.append(pos * (px[i] / px[i - 1] - 1 if px[i - 1] else 0.0) - cost)
    return rets


def run():
    from . import history
    from .crsp import query as crsp

    def series(s, start="2015-01-01"):
        return [b["close"] for b in crsp.get_prices(s, start, None) if b.get("close")]

    spy = series("SPY")
    out = {"SPY buy&hold": _metrics(buy_hold(spy))}
    out["Defensive rotation SPY/TLT"] = _metrics(defensive_rotation("SPY", "TLT"))
    out["Defensive rotation QQQ/TLT"] = _metrics(defensive_rotation("QQQ", "TLT"))
    bigcap = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM",
              "UNH", "JNJ", "WMT", "PG", "HD", "V", "KO"]
    out["XS momentum long top5"] = _metrics(xs_momentum_long(bigcap, top_n=5))
    out["SPY vol-target trend"] = _metrics(vol_target_trend(spy))

    # crypto trend / rotation via CoinEx
    try:
        cp = history.load_panel(["BTC/USD", "ETH/USD", "SOL/USD"], days=1000, source="coinex")
        common = cp["dates"]
        btc = cp["prices"].get("BTC/USD", [])
        if len(btc) > 220:
            out["BTC buy&hold"] = _metrics(buy_hold(btc))
            out["BTC vol-target trend"] = _metrics(vol_target_trend(btc, n=100, target=0.4))
    except Exception as e:  # noqa: BLE001
        out["crypto_error"] = str(e)[:100]

    base = out["SPY buy&hold"]
    ranked = sorted(((k, v) for k, v in out.items() if isinstance(v, dict)),
                    key=lambda kv: kv[1]["sharpe"], reverse=True)
    print(f"{'strategy':30s} {'total%':>9} {'sharpe':>7} {'maxDD%':>8} {'beats(Sharpe)':>13}")
    for k, v in ranked:
        beat = "YES" if v["sharpe"] > base["sharpe"] else "no"
        print(f"{k:30s} {v['total']:9.2f} {v['sharpe']:7.2f} {v['maxdd']:8.2f} {beat:>13}")
    return {"results": out, "ranked": [k for k, _ in ranked]}


if __name__ == "__main__":
    run()
