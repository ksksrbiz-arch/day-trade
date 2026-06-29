"""Live-signal scorecard.

Every directional CALL the system makes -- from confluence, the ML model, WSB
buzz, the council, news labels -- is recorded with the symbol, side, and the
price at the time. Later, reconcile() looks up the realized forward return and
scores each SOURCE on hit-rate and average forward return. This is how we find
out which of our *own* signals actually predict anything -- the edge that can't
be backtested historically because these signals didn't exist in the past.

SQLite-backed, pure stdlib, fail-soft.
"""
from __future__ import annotations

import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "signals"))
DB = os.environ.get("SIGNAL_DB", os.path.join(_DATA, "signals.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, day TEXT, source TEXT, symbol TEXT, side TEXT,
    strength REAL, ref_price REAL,
    horizon INTEGER, status TEXT DEFAULT 'open',
    fwd_ret REAL, hit INTEGER, resolved_ts TEXT
);
CREATE INDEX IF NOT EXISTS ix_sig_open ON signals(status);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def conn():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def record(source: str, symbol: str, side: str, ref_price: float | None,
           strength: float = 0.0, horizon: int = 5, dedup_day: bool = True) -> bool:
    """Log one directional signal. dedup_day avoids double-logging the same
    source/symbol/side within a calendar day."""
    if side not in ("buy", "sell") or not symbol:
        return False
    c = conn()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if dedup_day:
        ex = c.execute("SELECT 1 FROM signals WHERE source=? AND symbol=? AND side=? AND day=?",
                       (source, symbol.upper(), side, day)).fetchone()
        if ex:
            c.close(); return False
    c.execute("INSERT INTO signals(ts,day,source,symbol,side,strength,ref_price,horizon,status)"
              " VALUES(?,?,?,?,?,?,?,?, 'open')",
              (_now(), day, source, symbol.upper(), side, float(strength),
               float(ref_price) if ref_price else None, int(horizon)))
    c.commit(); c.close()
    return True


def reconcile(max_rows: int = 500) -> dict:
    """Resolve open signals whose horizon has matured, using CRSP-lite prices."""
    try:
        from .crsp import query as crsp
    except Exception:  # noqa: BLE001
        return {"resolved": 0, "note": "crsp unavailable"}
    c = conn()
    rows = c.execute("SELECT * FROM signals WHERE status='open' ORDER BY id LIMIT ?",
                     (max_rows,)).fetchall()
    resolved = 0
    for r in rows:
        sym, day, hz = r["symbol"], r["day"], r["horizon"]
        if "/" in sym:
            continue
        try:
            bars = crsp.get_prices(sym, "2024-01-01", None)
        except Exception:  # noqa: BLE001
            continue
        closes = [b["close"] for b in bars if b.get("close")]
        ds = [b["date"] for b in bars if b.get("close")]
        idx = next((i for i, d in enumerate(ds) if d >= day), None)
        if idx is None or idx + hz >= len(closes):
            continue                       # not matured yet
        p0 = r["ref_price"] or closes[idx]
        fwd = closes[idx + hz] / p0 - 1.0 if p0 else 0.0
        hit = 1 if ((fwd > 0) == (r["side"] == "buy")) else 0
        cc = conn()
        cc.execute("UPDATE signals SET status='resolved', fwd_ret=?, hit=?, resolved_ts=? WHERE id=?",
                   (round(fwd, 4), hit, _now(), r["id"]))
        cc.commit(); cc.close()
        resolved += 1
    c.close()
    return {"resolved": resolved}


def scoreboard() -> dict:
    c = conn()
    rows = c.execute(
        "SELECT source, COUNT(*) n, "
        "SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) resolved, "
        "AVG(CASE WHEN status='resolved' THEN hit END) hit_rate, "
        "AVG(CASE WHEN status='resolved' THEN "
        "  (CASE WHEN side='buy' THEN fwd_ret ELSE -fwd_ret END) END) avg_dir_ret "
        "FROM signals GROUP BY source ORDER BY n DESC").fetchall()
    c.close()
    out = []
    for r in rows:
        out.append({"source": r["source"], "signals": r["n"], "resolved": r["resolved"],
                    "hit_rate": round(r["hit_rate"], 3) if r["hit_rate"] is not None else None,
                    "avg_dir_return_pct": round(r["avg_dir_ret"] * 100, 3) if r["avg_dir_ret"] is not None else None})
    return {"by_source": out}


if __name__ == "__main__":
    record("test", "AAPL", "buy", 200.0, 0.5)
    record("test", "XOM", "sell", 110.0, -0.4)
    print("reconcile:", reconcile())
    import json
    print(json.dumps(scoreboard(), indent=2))
