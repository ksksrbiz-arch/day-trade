"""
Walk-forward backtest harness for the cross-sectional ranking strategy.

Why walk-forward: it's the only honest way to know if a rule has an edge. We
pick the parameter (momentum lookback) on an IN-SAMPLE train window, then trade
it on the NEXT, unseen OUT-OF-SAMPLE window, and roll forward. Stitching the OOS
windows gives an equity curve that never saw its own future -- no curve-fitting.

Every fill pays a slippage haircut on turnover (default 10bps), so a do-nothing
strategy loses money on purpose. Beating SPY buy-and-hold over the stitched OOS
period, net of that haircut, is the bar. Most strategies fail it -- that's the
point of measuring.

Pure engine + metrics are unit-tested; the runner pulls real history.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from . import xsection as xs

PROJ = Path(__file__).resolve().parent.parent
OUT = PROJ / "data" / "backtests"

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "JPM", "BAC", "WFC", "GS", "V", "MA", "XOM", "CVX", "COP", "UNH",
    "JNJ", "PFE", "MRK", "LLY", "HD", "LOW", "WMT", "COST", "PG", "KO",
    "PEP", "DIS", "CRM", "ORCL", "INTC", "CSCO", "QCOM", "TXN", "CAT", "BA",
]
GRID = [20, 40, 60, 120]
CRYPTO_UNIVERSE = ["BTC/USD","ETH/USD","SOL/USD","ADA/USD","XRP/USD","DOGE/USD","LTC/USD","LINK/USD","AVAX/USD","DOT/USD","UNI/USD","ATOM/USD","BCH/USD","ETC/USD","XLM/USD"]


def _metrics(rets: list[float]) -> dict:
    if not rets:
        return {"total": 0, "ann": 0, "vol": 0, "sharpe": 0, "maxdd": 0, "days": 0}
    eq = 1.0
    curve = []
    for r in rets:
        eq *= (1 + r); curve.append(eq)
    total = eq - 1
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    sd = math.sqrt(var)
    ann = (1 + mean) ** 252 - 1
    annvol = sd * math.sqrt(252)
    sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else 0.0
    peak = -1e9; mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    return {"total": round(total * 100, 2), "ann": round(ann * 100, 2),
            "vol": round(annvol * 100, 2), "sharpe": round(sharpe, 2),
            "maxdd": round(mdd * 100, 2), "days": len(rets)}


def run_engine(prices, tradables, lo, hi, params) -> list[float]:
    """Daily portfolio returns for days (lo, hi]. Weights set with info up to t-1
    (no lookahead), applied to day t's return. Slippage on rebalance turnover."""
    lookback = params["lookback"]; vw = params.get("vol_window", 20)
    k = params["rebalance"]; topn = params["top_n"]
    allow_short = params["allow_short"]; slip = params.get("slippage_bps", 10) / 1e4
    weights: dict[str, float] = {}
    rets = []
    for idx, t in enumerate(range(lo, hi)):
        if idx % k == 0:
            scores = {s: xs.score(prices[s], t - 1, lookback, vw) for s in tradables}
            longs, shorts = xs.rank_select(scores, topn, allow_short)
            target = xs.target_weights(longs, shorts)
            turnover = sum(abs(target.get(s, 0) - weights.get(s, 0))
                           for s in set(target) | set(weights))
            cost = slip * turnover
            weights = target
        else:
            cost = 0.0
        rp = 0.0
        for s, w in weights.items():
            p0 = prices[s][t - 1]; p1 = prices[s][t]
            if p0 > 0:
                rp += w * (p1 / p0 - 1)
        rets.append(rp - cost)
    return rets


def _bench(spy, lo, hi) -> list[float]:
    return [spy[t] / spy[t - 1] - 1 for t in range(lo, hi)]


def walk_forward(panel, spy, tradables, train=378, test=126, base_params=None):
    base = {"vol_window": 20, "rebalance": 5, "top_n": 6, "allow_short": True,
            "slippage_bps": 10}
    if base_params:
        base.update(base_params)
    n = len(panel["dates"])
    # adapt windows to available history so short panels still yield folds
    if n < train + test:
        train = max(60, int(n * 0.6))
        test = max(20, int(n * 0.25))
    prices = panel["prices"]
    oos, bench, folds = [], [], []
    start = train
    while start + test <= n:
        tr_lo, tr_hi = start - train, start
        te_lo, te_hi = start, start + test
        # in-sample: pick best lookback by Sharpe
        best_lb, best_sh = GRID[0], -1e9
        for lb in GRID:
            p = {**base, "lookback": lb}
            m = _metrics(run_engine(prices, tradables, tr_lo + lb + 1, tr_hi, p))
            if m["sharpe"] > best_sh:
                best_sh, best_lb = m["sharpe"], lb
        # out-of-sample: trade chosen lookback
        p = {**base, "lookback": best_lb}
        r = run_engine(prices, tradables, te_lo, te_hi, p)
        b = _bench(spy, te_lo, te_hi)
        oos += r; bench += b
        folds.append({"test_from": panel["dates"][te_lo], "test_to": panel["dates"][te_hi - 1],
                      "lookback": best_lb, "oos": _metrics(r), "spy": _metrics(b)})
        start += test
    return {"oos": _metrics(oos), "spy": _metrics(bench), "folds": folds,
            "edge_vs_spy_pct": round(_metrics(oos)["total"] - _metrics(bench)["total"], 2)}


def pit_universe(asof: str, top: int = 60) -> list[str] | None:
    """Point-in-time S&P 500 members as-of `asof` from the CRSP-lite master
    (survivorship-bias-reduced: includes names later delisted). None on failure."""
    try:
        from trader.crsp import query as crsp
        names = crsp.constituents_asof(asof)
        return names[:top] if names else None
    except Exception:
        return None


def run(days=750, universe=None, allow_short=True, source="auto", benchmark=None, out="latest.json", pit_asof=None) -> dict:
    from trader import config
    from trader.massive import MassiveClient
    from trader import history
    cfg = config.load()
    if universe is None and pit_asof:
        universe = pit_universe(pit_asof)
    if universe is None:
        universe = CRYPTO_UNIVERSE if source == "binance" else DEFAULT_UNIVERSE
    if benchmark is None:
        benchmark = "BTC/USD" if source == "binance" else "SPY"
    syms = list(dict.fromkeys(universe + [benchmark]))
    massive = MassiveClient(cfg.massive_access, cfg.massive_secret, cfg.massive_endpoint, cfg.massive_bucket)
    panel = history.load_panel(syms, days=days, key=cfg.alpaca_key, secret=cfg.alpaca_secret, massive=massive, source=source, tiingo_token=cfg.tiingo_token)
    spy = panel["prices"].pop(benchmark, None)
    if not spy:
        return {"oos": _metrics([]), "spy": _metrics([]), "folds": [],
                "edge_vs_spy_pct": 0.0,
                "meta": {"source": panel.get("source"), "symbols": 0,
                         "from": None, "to": None, "error": f"benchmark {benchmark} unavailable"}}
    tradables = [s for s in universe if s in panel["prices"] and len(panel["prices"][s]) == len(panel["dates"])]
    res = walk_forward(panel, spy, tradables, base_params={"allow_short": allow_short})
    res["meta"] = {"source": panel["source"], "symbols": len(tradables),
                   "from": panel["dates"][0] if panel["dates"] else None,
                   "to": panel["dates"][-1] if panel["dates"] else None,
                   "generated": datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / out).write_text(json.dumps(res, indent=2))
    return res


def _fmt(res) -> str:
    m = res["meta"]; o = res["oos"]; s = res["spy"]
    L = [f"WALK-FORWARD BACKTEST  ({m['source']} data, {m['symbols']} names, {m['from']} -> {m['to']})", ""]
    L.append("Stitched OUT-OF-SAMPLE vs SPY buy-and-hold (net of 10bps slippage):")
    L.append(f"  Strategy : total {o['total']:+.2f}%  ann {o['ann']:+.2f}%  Sharpe {o['sharpe']}  maxDD {o['maxdd']}%")
    L.append(f"  SPY      : total {s['total']:+.2f}%  ann {s['ann']:+.2f}%  Sharpe {s['sharpe']}  maxDD {s['maxdd']}%")
    L.append(f"  EDGE     : {res['edge_vs_spy_pct']:+.2f}%  over {o['days']} OOS days")
    L.append("")
    L.append("Per fold (chosen lookback picked in-sample, traded out-of-sample):")
    for f in res["folds"]:
        L.append(f"  {f['test_from']}->{f['test_to']}  lb={f['lookback']:3}  "
                 f"strat {f['oos']['total']:+6.2f}%  spy {f['spy']['total']:+6.2f}%")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    days, allow_short = 750, True
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
        if a == "--long-only":
            allow_short = False
        if a == "--short":
            allow_short = True
    source = "auto"
    for i, a in enumerate(sys.argv):
        if a == "--source" and i + 1 < len(sys.argv):
            source = sys.argv[i + 1]
    pit = None
    for i, a in enumerate(sys.argv):
        if a == "--pit" and i + 1 < len(sys.argv):
            pit = sys.argv[i + 1]
    r = run(days=days, allow_short=allow_short, source=source, pit_asof=pit)
    print(_fmt(r))
