"""Decision reasoning trace -- the 'why' behind every conviction.

The confluence brain blends voices into one number; this module captures the
full rationale at decision time so the desk (and the dashboard) can answer
'why did we lean long on NVDA?': which voices spoke, their scores, the effective
weights after regime/learned emphasis, the agreement count, and the verdict.

Self-contained SQLite, idempotent per (symbol, day, side, rounded-composite) so
repeated analysis of the same name doesn't spam the trace. Fail-soft throughout.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "reasoning"))
DB = os.environ.get("REASONING_DB", os.path.join(_DATA, "reasoning.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, ts TEXT, day TEXT, symbol TEXT, side TEXT,
  composite REAL, agree INTEGER, n INTEGER, gate INTEGER, size_mult REAL,
  regime TEXT, scores TEXT, weights TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_dec_ts ON decisions(ts);
"""


def _conn():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record(symbol: str, conv, regime: str | None = None) -> bool:
    """Persist a conviction's full rationale. Returns True if newly stored.
    `conv` is an alpha.Conviction (has composite/side/agree/scores/weights/...)."""
    try:
        sym = (symbol or "?").upper()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        comp = round(float(getattr(conv, "composite", 0.0)), 2)
        side = getattr(conv, "side", "flat")
        key = f"{sym}|{day}|{side}|{comp}"
        aid = hashlib.sha1(key.encode()).hexdigest()[:16]
        c = _conn()
        cur = c.execute(
            "INSERT OR IGNORE INTO decisions"
            "(id,ts,day,symbol,side,composite,agree,n,gate,size_mult,regime,scores,weights,reason)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, _now(), day, sym, side, float(getattr(conv, "composite", 0.0)),
             int(getattr(conv, "agree", 0)), int(getattr(conv, "n_methods", 0)),
             1 if getattr(conv, "gate_pass", False) else 0,
             float(getattr(conv, "size_mult", 0.0)), regime or "n/a",
             json.dumps(getattr(conv, "scores", {})), json.dumps(getattr(conv, "weights", {})),
             (getattr(conv, "reason", "") or "")[:300]))
        new = cur.rowcount > 0
        c.commit(); c.close()
        # surface notable, newly-gated convictions to the shared mesh
        if new and getattr(conv, "gate_pass", False) and abs(float(getattr(conv, "composite", 0.0))) >= 0.30:
            try:
                from . import mesh
                sc = getattr(conv, "scores", {}) or {}
                drivers = ", ".join(f"{k} {v:+.2f}" for k, v in
                                    sorted(sc.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3])
                mesh.publish("reasoning", "decision",
                             f"{sym} {side} {float(getattr(conv,'composite',0)):+.2f} "
                             f"(agree {getattr(conv,'agree',0)}/{getattr(conv,'n_methods',0)}; {drivers})",
                             symbol=sym, salience=0.6)
            except Exception:  # noqa: BLE001
                pass
        return new
    except Exception:  # noqa: BLE001
        return False


def recent(limit: int = 50, symbol: str | None = None, gated_only: bool = False) -> list[dict]:
    try:
        c = _conn()
        q = "SELECT * FROM decisions"
        cond, args = [], []
        if symbol:
            cond.append("symbol=?"); args.append(symbol.upper())
        if gated_only:
            cond.append("gate=1")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(q, args).fetchall()
        c.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["scores"] = json.loads(d["scores"]); d["weights"] = json.loads(d["weights"])
            except Exception:  # noqa: BLE001
                pass
            out.append(d)
        return out
    except Exception:  # noqa: BLE001
        return []


def voice_leaderboard(limit_days: int = 14) -> list[dict]:
    """Across recent decisions, how often each voice was the TOP contributor
    (|weight*score| largest) and its average signed contribution. A compact read
    on which voices are actually driving the desk's convictions."""
    rows = recent(limit=400)
    agg: dict[str, dict] = {}
    for d in rows:
        sc, w = d.get("scores", {}), d.get("weights", {})
        if not isinstance(sc, dict) or not isinstance(w, dict):
            continue
        contrib = {k: w.get(k, 0.0) * sc.get(k, 0.0) for k in sc}
        if not contrib:
            continue
        top = max(contrib, key=lambda k: abs(contrib[k]))
        for k, v in contrib.items():
            a = agg.setdefault(k, {"voice": k, "top": 0, "sum": 0.0, "n": 0})
            a["sum"] += v; a["n"] += 1
        agg[top]["top"] += 1
    out = [{"voice": k, "top_count": a["top"], "n": a["n"],
            "avg_contrib": round(a["sum"] / a["n"], 4) if a["n"] else 0.0}
           for k, a in agg.items()]
    out.sort(key=lambda x: x["top_count"], reverse=True)
    return out


def stats() -> dict:
    try:
        c = _conn()
        tot = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        gated = c.execute("SELECT COUNT(*) FROM decisions WHERE gate=1").fetchone()[0]
        buys = c.execute("SELECT COUNT(*) FROM decisions WHERE side='buy'").fetchone()[0]
        sells = c.execute("SELECT COUNT(*) FROM decisions WHERE side='sell'").fetchone()[0]
        # conviction-magnitude histogram (|composite|)
        rows = c.execute("SELECT composite FROM decisions ORDER BY ts DESC LIMIT 500").fetchall()
        c.close()
        edges = [0.0, 0.1, 0.2, 0.3, 0.5, 1.01]
        labels = ["0-.1", ".1-.2", ".2-.3", ".3-.5", ".5+"]
        hist = {l: 0 for l in labels}
        for r in rows:
            a = abs(float(r[0] or 0.0))
            for i in range(len(labels)):
                if edges[i] <= a < edges[i + 1]:
                    hist[labels[i]] += 1
                    break
        return {"total": tot, "gated": gated,
                "pass_rate": round(gated / tot, 3) if tot else 0.0,
                "buys": buys, "sells": sells,
                "conviction_hist": [{"bucket": l, "n": hist[l]} for l in labels]}
    except Exception:  # noqa: BLE001
        return {"total": 0, "gated": 0, "pass_rate": 0.0}


if __name__ == "__main__":
    print(stats())
    for d in recent(8):
        print(f"  {d['symbol']:6} {d['side']:4} {d['composite']:+.2f} gate={d['gate']} :: {d['reason'][:80]}")
