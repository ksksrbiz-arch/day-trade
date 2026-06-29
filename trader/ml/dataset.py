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
                  conn=None):
    """X, y, dates, syms, names. Label=1 if forward `horizon`-day return>threshold."""
    own = conn is None
    conn = conn or connect()
    series = _series_by_symbol(conn, min_len=lookback + horizon + 5)
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
            X.append(vec)
            y.append(1 if fwd > threshold else 0)
            dates.append(dts[t - 1])
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
