"""Prediction store + decision matrix (SQLite, idempotent).

predictions : every hypothesis we chose to watch, with its features, the
              matrix's prior probability, and -- once matured -- the outcome.
patterns    : the DECISION MATRIX. Resolved predictions are aggregated into
              feature BUCKETS (source x direction x horizon x regime x magnitude)
              giving a hit-rate + average return per bucket. This is the indexable
              "what actually happens" knowledge the platform predicts from.

Idempotency: a prediction id is a content hash, so re-ingesting the same post is
a no-op; the matrix is REBUILT from resolved rows, so aggregation never double
counts.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "predict"))
DB = os.environ.get("PREDICT_DB", os.path.join(_DATA, "predict.db"))
MIN_N = 5   # minimum bucket samples before the matrix is trusted

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id TEXT PRIMARY KEY,
    ts TEXT, day TEXT, source TEXT, symbol TEXT, asset TEXT,
    direction TEXT, magnitude_pct REAL, horizon_days INTEGER,
    rationale TEXT, ai_confidence REAL, regime TEXT,
    ref_price REAL, target_price REAL,
    matrix_prob REAL, rank_score REAL,
    status TEXT DEFAULT 'watching',
    outcome_ret REAL, hit INTEGER, resolved_ts TEXT
);
CREATE INDEX IF NOT EXISTS ix_pred_status ON predictions(status);
CREATE INDEX IF NOT EXISTS ix_pred_symbol ON predictions(symbol);

CREATE TABLE IF NOT EXISTS patterns (
    key TEXT PRIMARY KEY,
    n INTEGER, hits INTEGER, hit_rate REAL, avg_ret REAL, updated TEXT
);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


# ---- feature bucketing (the matrix index) --------------------------------- #
def _hbucket(h):
    return "d1-3" if h <= 3 else ("d4-7" if h <= 7 else "d8+")


def _mbucket(m):
    m = abs(m or 0)
    return "m0-5" if m < 5 else ("m5-15" if m < 15 else "m15+")


def bucket_key(source, direction, horizon, magnitude, regime):
    return f"{source}|{direction}|{_hbucket(horizon)}|{_mbucket(magnitude)}|{regime or 'na'}"


def _pid(source, symbol, direction, day, rationale):
    # dedup one prediction per source/symbol/direction/day (rationale excluded
    # so near-identical re-posts of the same call collapse cleanly)
    raw = f"{source}|{symbol}|{direction}|{day}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ---- write ---------------------------------------------------------------- #
def record_prediction(source, symbol, direction, horizon_days, magnitude_pct,
                      rationale, ai_confidence, regime, ref_price, asset="equity"):
    """Idempotent insert of a watched prediction. Returns (id, created?)."""
    if direction not in ("up", "down") or not symbol:
        return None, False
    day = time.strftime("%Y-%m-%d", time.gmtime())
    pid = _pid(source, symbol.upper(), direction, day, rationale)
    target = None
    if ref_price:
        target = ref_price * (1 + (magnitude_pct or 0) / 100 * (1 if direction == "up" else -1))
    ms = matrix_score(bucket_key(source, direction, horizon_days, magnitude_pct, regime))
    prob = ms["prob"]
    # rank = blend of AI confidence and the matrix's historical probability,
    # weighted by how much evidence the matrix has for this bucket.
    w = ms["confidence"]
    rank = round((w * prob + (1 - w) * float(ai_confidence or 0.5)), 4)
    c = connect()
    cur = c.execute(
        "INSERT OR IGNORE INTO predictions(id,ts,day,source,symbol,asset,direction,"
        "magnitude_pct,horizon_days,rationale,ai_confidence,regime,ref_price,"
        "target_price,matrix_prob,rank_score,status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'watching')",
        (pid, _now(), day, source, symbol.upper(), asset, direction,
         float(magnitude_pct or 0), int(horizon_days or 5), (rationale or "")[:300],
         float(ai_confidence or 0.5), regime or "na",
         float(ref_price) if ref_price else None,
         round(target, 4) if target else None, round(prob, 4), rank))
    created = cur.rowcount > 0
    c.commit(); c.close()
    return pid, created


# ---- decision matrix ------------------------------------------------------ #
def matrix_score(key: str) -> dict:
    """Historical probability for a bucket key. Falls back to 0.5 / low
    confidence when the bucket is thin (keeps the matrix 'lucid')."""
    c = connect()
    r = c.execute("SELECT n,hit_rate,avg_ret FROM patterns WHERE key=?", (key,)).fetchone()
    c.close()
    if not r or r["n"] < MIN_N:
        n = r["n"] if r else 0
        return {"prob": 0.5, "n": n, "confidence": round(min(0.4, n / 20), 3),
                "avg_ret": (r["avg_ret"] if r else 0.0)}
    conf = round(min(0.95, r["n"] / (r["n"] + 12)), 3)
    return {"prob": round(r["hit_rate"], 4), "n": r["n"], "confidence": conf,
            "avg_ret": round(r["avg_ret"], 4)}


def rebuild_matrix() -> dict:
    """Recompute all buckets from resolved predictions (idempotent)."""
    c = connect()
    rows = c.execute("SELECT source,direction,horizon_days,magnitude_pct,regime,"
                     "hit,outcome_ret FROM predictions WHERE status IN ('correct','incorrect')").fetchall()
    agg: dict[str, list] = {}
    for r in rows:
        k = bucket_key(r["source"], r["direction"], r["horizon_days"],
                       r["magnitude_pct"], r["regime"])
        a = agg.setdefault(k, [0, 0, 0.0])
        a[0] += 1; a[1] += (r["hit"] or 0); a[2] += (r["outcome_ret"] or 0.0)
    c.execute("DELETE FROM patterns")
    for k, (n, hits, sret) in agg.items():
        c.execute("INSERT INTO patterns(key,n,hits,hit_rate,avg_ret,updated) VALUES(?,?,?,?,?,?)",
                  (k, n, hits, round(hits / n, 4), round(sret / n, 4), _now()))
    c.commit(); c.close()
    return {"buckets": len(agg), "resolved": len(rows)}


# ---- resolution ----------------------------------------------------------- #
def resolve_due(price_fn) -> dict:
    """Resolve watching predictions whose horizon has matured.
    price_fn(symbol, asset, day, horizon) -> (ref_price, future_price) or None."""
    c = connect()
    rows = c.execute("SELECT * FROM predictions WHERE status='watching'").fetchall()
    done = 0
    for r in rows:
        res = price_fn(r["symbol"], r["asset"], r["day"], r["horizon_days"])
        if not res:
            continue
        ref, fut = res
        if not ref:
            continue
        ret = fut / ref - 1.0
        # "correct" = moved the predicted direction by at least a third of the
        # forecast magnitude (so a flat tape doesn't count as a win).
        thresh = max(0.005, (r["magnitude_pct"] or 0) / 100 * 0.33)
        if r["direction"] == "up":
            hit = 1 if ret >= thresh else 0
        else:
            hit = 1 if ret <= -thresh else 0
        status = "correct" if hit else "incorrect"
        cc = connect()
        cc.execute("UPDATE predictions SET status=?, outcome_ret=?, hit=?, resolved_ts=? WHERE id=?",
                   (status, round(ret, 4), hit, _now(), r["id"]))
        cc.commit(); cc.close()
        done += 1
    c.close()
    if done:
        rebuild_matrix()
    return {"resolved": done}


# ---- reads (dashboard) ---------------------------------------------------- #
def predictions(status=None, limit=60):
    c = connect()
    if status:
        rows = c.execute("SELECT * FROM predictions WHERE status=? ORDER BY rank_score DESC LIMIT ?",
                         (status, limit)).fetchall()
    else:
        rows = c.execute("SELECT * FROM predictions ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def decision_matrix(min_n=1):
    c = connect()
    rows = c.execute("SELECT * FROM patterns WHERE n>=? ORDER BY n DESC", (min_n,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def stats():
    c = connect()
    g = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
    out = {"total": g("SELECT COUNT(*) FROM predictions"),
           "watching": g("SELECT COUNT(*) FROM predictions WHERE status='watching'"),
           "correct": g("SELECT COUNT(*) FROM predictions WHERE status='correct'"),
           "incorrect": g("SELECT COUNT(*) FROM predictions WHERE status='incorrect'"),
           "buckets": g("SELECT COUNT(*) FROM patterns")}
    c.close()
    return out


if __name__ == "__main__":
    import json
    pid, created = record_prediction("wsb", "WEN", "up", 5, 10, "fries pump", 0.7,
                                     "risk_on", 20.0)
    print("recorded", pid, created)
    print("matrix_score sample:", matrix_score(bucket_key("wsb", "up", 5, 10, "risk_on")))
    print("stats:", json.dumps(stats()))
