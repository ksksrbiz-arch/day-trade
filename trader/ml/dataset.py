"""Build a supervised training set from the CRSP-lite price cache.

For every cached symbol we slide a window over its history: at each step we
compute features from the prices *up to* day t (no lookahead) and label it with
the sign of the forward `horizon`-day return *after* t. Because the price
universe is survivorship-bias-reduced (it includes names later delisted), the
model learns from winners AND losers -- not just today's survivors.

Returns X (list of vectors), y (0/1), dates (asof), syms, feature_names.
"""
from __future__ import annotations

from .features import feature_vector, FEATURES
from ..crsp.schema import connect


def _series_by_symbol(conn, min_len: int):
    rows = conn.execute(
        "SELECT s.ticker AS tk, p.date AS d, p.adj_close AS c "
        "FROM prices p JOIN securities s ON s.permno=p.permno "
        "WHERE p.adj_close IS NOT NULL ORDER BY s.ticker, p.date").fetchall()
    series: dict[str, list[tuple]] = {}
    for r in rows:
        series.setdefault(r["tk"], []).append((r["d"], r["c"]))
    return {k: v for k, v in series.items() if len(v) >= min_len}


def build_dataset(horizon: int = 10, lookback: int = 60, step: int = 3,
                  threshold: float = 0.0, max_per_symbol: int = 400,
                  conn=None, market_relative: bool = True, neutral_band: float = 0.01):
    """X, y, dates, syms, names.

    Label = 1 if the sample's EXCESS forward return (stock minus market over the
    same `horizon`) is positive, when ``market_relative`` (default) -- otherwise
    raw direction. Samples whose |excess| < ``neutral_band`` are DROPPED so the
    model trains on decisive moves, not near-zero noise. This targets
    cross-sectional alpha instead of market beta, which is where price-based TA
    features carry real signal."""
    own = conn is None
    try:                                  # CRSP may be absent (cloud) -> guard it
        conn = conn or connect()
        series = _series_by_symbol(conn, min_len=lookback + horizon + 5)
    except Exception:  # noqa: BLE001
        series, own = {}, False
    if not series:                        # empty/absent CRSP -> Alpaca IEX daily bars
        series = _series_from_alpaca(min_len=lookback + horizon + 5)
    bench = _benchmark_fwd_by_date(horizon) if market_relative else {}
    X, y, dates, syms = [], [], [], []
    for tk, sv in series.items():
        closes = [c for _, c in sv]
        dts = [d for d, _ in sv]
        n = len(closes)
        cnt = 0
        # need lookback history before t and horizon after t
        for t in range(lookback, n - horizon, step):
            window = closes[t - lookback:t]
            vec, _ = feature_vector(window)
            if vec is None:
                continue
            p0, p1 = closes[t - 1], closes[t - 1 + horizon]
            if not p0:
                continue
            fwd = p1 / p0 - 1.0
            asof = dts[t - 1]
            base = bench.get(asof, 0.0) if market_relative else 0.0
            excess = fwd - base - threshold
            if neutral_band and abs(excess) < neutral_band:
                continue                       # drop ambiguous near-zero moves
            X.append(vec)
            y.append(1 if excess > 0 else 0)
            dates.append(asof)
            syms.append(tk)
            cnt += 1
            if cnt >= max_per_symbol:
                break
    if own:
        conn.close()
    return X, y, dates, syms, FEATURES


if __name__ == "__main__":
    X, y, d, s, names = build_dataset()
    pos = sum(y)
    print(f"samples={len(X)}  features={len(names)}  positives={pos} "
          f"({(100*pos/len(y) if y else 0):.1f}%)  symbols={len(set(s))}")
    if d:
        print("date range:", min(d), "->", max(d))


# --- cloud fallback: build training series from Alpaca IEX daily bars when the
# local CRSP price cache is empty (e.g. a fresh container). Cached per process. ---
_ALP_UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
                 "META", "TSLA", "JPM", "XOM", "UNH", "GLD", "TLT", "HYG", "AMD",
                 "NFLX", "BAC", "WMT", "COST", "AVGO", "CRM"]
_ALP_SERIES_CACHE = {"ts": 0.0, "val": None}


_SYM_SERIES_CACHE: dict = {}          # sym -> (ts, series) ; memoize so a backfill
                                      # resolving thousands of rows only fetches once.

def _alpaca_series(sym, limit=400, ttl=3600):
    import os, json, datetime, urllib.request, urllib.parse, time as _t
    hit = _SYM_SERIES_CACHE.get(sym)
    if hit and (_t.time() - hit[0]) < ttl:
        return hit[1]
    k = os.environ.get("ALPACA_API_KEY", ""); sec = os.environ.get("ALPACA_SECRET_KEY", "")
    if not k or not sec:
        return []
    try:
        start = (datetime.date.today() - datetime.timedelta(days=700)).isoformat()
        q = urllib.parse.urlencode({"timeframe": "1Day", "limit": limit, "adjustment": "raw",
                                    "feed": "iex", "start": start})
        req = urllib.request.Request(f"https://data.alpaca.markets/v2/stocks/{sym}/bars?{q}",
                                     headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": sec,
                                              "User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        out = [(b["t"][:10], b["c"]) for b in (d.get("bars") or []) if "c" in b and "t" in b]
        if out:
            _SYM_SERIES_CACHE[sym] = (_t.time(), out)
        return out
    except Exception:  # noqa: BLE001
        return []



def _benchmark_fwd_by_date(horizon: int, symbol: str = "SPY") -> dict:
    """Forward `horizon`-day return of the market proxy, keyed by START date.
    Lets the dataset label a stock by whether it BEATS the market (excess return)
    instead of raw direction -- removing the dominant market-beta component so the
    model can learn the idiosyncratic/cross-sectional signal TA actually carries."""
    try:
        spy = _alpaca_series(symbol)
    except Exception:  # noqa: BLE001
        spy = []
    m: dict = {}
    if len(spy) > horizon:
        dates = [d for d, _ in spy]
        closes = [c for _, c in spy]
        for i in range(len(closes) - horizon):
            p0 = closes[i]
            if p0:
                m[dates[i]] = closes[i + horizon] / p0 - 1.0
    return m


def _series_from_alpaca(min_len):
    import time as _t
    if _ALP_SERIES_CACHE["val"] is not None and (_t.time() - _ALP_SERIES_CACHE["ts"]) < 3600:
        base = _ALP_SERIES_CACHE["val"]
    else:
        base = {}
        for sym in _ALP_UNIVERSE:
            sv = _alpaca_series(sym)
            if len(sv) >= min_len:
                base[sym] = sv
        _ALP_SERIES_CACHE["ts"] = _t.time(); _ALP_SERIES_CACHE["val"] = base
    return dict(base)

