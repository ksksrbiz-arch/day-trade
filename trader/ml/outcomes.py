"""ML learns from the system's OWN trades.

Reads every bot ledger, and for each real entry (action='order') reconstructs
the entry-time feature vector from price history *as of that date* and labels it
with the realized forward return. These samples are merged into the CRSP-lite
base set, so retraining teaches the model from how the bots actually behaved --
a closed feedback loop between the live bots and the learning layer.
"""
from __future__ import annotations

import csv
import glob
import os

from .features import feature_vector

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _ledgers() -> list[str]:
    pats = [os.path.join(_PROJ, "data", "bots", "*", "trades.csv"),
            os.path.join(_PROJ, "data", "*trades*.csv")]
    out = []
    for p in pats:
        out.extend(glob.glob(p))
    return out


def trade_samples(horizon: int = 10, lookback: int = 60, threshold: float = 0.0):
    """Return X, y, dates, syms from real order rows + realized forward returns."""
    try:
        from ..crsp import query as crsp
    except Exception:  # noqa: BLE001
        return [], [], [], []
    X, y, dates, syms = [], [], [], []
    seen = set()
    for path in _ledgers():
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            if r.get("action") != "order":
                continue
            sym = (r.get("symbol") or "").upper()
            if not sym or "/" in sym:        # skip crypto (different price source)
                continue
            day = (r.get("ts") or "")[:10]
            if not day:
                continue
            key = (sym, day)
            if key in seen:
                continue
            seen.add(key)
            try:
                bars = crsp.get_prices(sym, "2018-01-01", None)
            except Exception:  # noqa: BLE001
                continue
            closes = [b["close"] for b in bars if b.get("close")]
            ds = [b["date"] for b in bars if b.get("close")]
            if len(closes) < lookback + horizon:
                continue
            # find index of entry day (first bar on/after the entry date)
            idx = next((i for i, dd in enumerate(ds) if dd >= day), None)
            if idx is None or idx < lookback or idx + horizon >= len(closes):
                continue
            vec, _ = feature_vector(closes[idx - lookback:idx])
            if vec is None:
                continue
            p0, p1 = closes[idx], closes[idx + horizon]
            if not p0:
                continue
            fwd = p1 / p0 - 1.0
            X.append(vec); y.append(1 if fwd > threshold else 0)
            dates.append(day); syms.append(sym)
    return X, y, dates, syms


if __name__ == "__main__":
    X, y, d, s = trade_samples()
    print(f"trade samples={len(X)} positives={sum(y) if y else 0} symbols={len(set(s))}")
    if d:
        print("range", min(d), "->", max(d))
