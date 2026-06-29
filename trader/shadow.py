"""Shadow Lab -- a live counterfactual configuration bake-off.

The platform can measure forward edge for ONE live configuration. The Shadow Lab
runs several alternative configurations in parallel against the SAME market and
resolves each forward, answering the question single-config measurement can't:
*which configuration's signals actually make money?*

Each "book" is a different way of turning the confluence per-method scores into a
directional position:

  live        -- the desk's actual blended composite (what we really trade)
  equal       -- equal weight across whichever voices have an opinion
  ta_only / quant_only / ml_only / tnet_only / prediction_only -- single-voice
  spy_hold    -- passive SPY buy-and-hold benchmark

Crucially, one alpha.analyze() per symbol produces the per-method scores that ALL
score-based books re-blend -- so the whole lab costs one analysis per name. Each
cycle records each book's position per symbol (idempotent per book/symbol/day);
matured signals are resolved against realized forward returns; standings() ranks
the books. This is a continuous, forward-only experiment -- it cannot be
backtested into looking good, and it gives the autonomy controller real
comparative evidence instead of waiting on a single config to mature.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time

UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
HORIZON = 5            # trading days
THR = 0.15            # min |composite| to take a position
_VOICES = ["ta", "quant", "fundamental", "ml", "council", "prediction", "tnet"]

# book -> weight map over voices (None = use the live composite directly)
BOOKS: dict[str, dict | None] = {
    "live": None,
    "equal": {v: 1.0 for v in _VOICES},
    "ta_only": {"ta": 1.0},
    "quant_only": {"quant": 1.0},
    "ml_only": {"ml": 1.0},
    "tnet_only": {"tnet": 1.0},
    "prediction_only": {"prediction": 1.0},
    "cortex": "neural",    # special: nonlinear MLP fuser over the voice scores
    "spy_hold": None,      # special: always long SPY
}

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "shadow"))
DB = os.environ.get("SHADOW_DB", os.path.join(_DATA, "shadow.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snaps (
  id TEXT PRIMARY KEY, ts TEXT, day TEXT, book TEXT, symbol TEXT,
  position REAL, ref_price REAL, horizon INTEGER,
  resolved INTEGER DEFAULT 0, realized_pct REAL
);
CREATE INDEX IF NOT EXISTS ix_shadow_book ON snaps(book);
CREATE INDEX IF NOT EXISTS ix_shadow_res ON snaps(resolved);
"""


def _conn():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def blend(scores: dict, weights: dict | None) -> float:
    """Re-blend per-method scores under a book's weights; normalized over the
    voices that actually have an opinion. Returns a composite in [-1,1]."""
    present = {k: float(v) for k, v in (scores or {}).items() if v is not None}
    if not present:
        return 0.0
    if weights is None:
        return 0.0
    w = {k: weights[k] for k in present if k in weights}
    tot = sum(abs(x) for x in w.values())
    if tot == 0:
        return 0.0
    return max(-1.0, min(1.0, sum(w[k] * present[k] for k in w) / tot))


def _position(composite: float) -> float:
    return 1.0 if composite >= THR else -1.0 if composite <= -THR else 0.0


def snapshot(universe=None, horizon: int = HORIZON) -> dict:
    """Record today's position for every book × symbol (idempotent per day)."""
    universe = universe or UNIVERSE
    day = time.strftime("%Y-%m-%d", time.gmtime())
    c = _conn()
    if c.execute("SELECT 1 FROM snaps WHERE day=? LIMIT 1", (day,)).fetchone():
        c.close()
        return {"day": day, "written": 0, "skipped": "already snapped today"}
    written = 0

    def put(book, symbol, pos, ref):
        nonlocal written
        sid = hashlib.sha1(f"{book}|{symbol}|{day}".encode()).hexdigest()[:16]
        cur = c.execute("INSERT OR IGNORE INTO snaps(id,ts,day,book,symbol,position,"
                        "ref_price,horizon,resolved,realized_pct) VALUES(?,?,?,?,?,?,?,?,0,NULL)",
                        (sid, _now(), day, book, symbol, float(pos), float(ref), int(horizon)))
        written += cur.rowcount

    try:
        from . import alpha
        from .crsp import query as crsp
    except Exception:  # noqa: BLE001
        c.close()
        return {"error": "imports unavailable"}

    for sym in universe:
        try:
            bars = crsp.get_prices(sym, "2024-06-01", None)
            closes = [b["close"] for b in bars if b.get("close")]
            if len(closes) < 30:
                continue
            last = float(closes[-1])
            conv = alpha.analyze(closes, symbol=sym)
            scores = conv.scores or {}
            for book, weights in BOOKS.items():
                if book == "spy_hold":
                    continue
                if book == "live":
                    comp = conv.composite
                elif book == "cortex":
                    try:
                        from . import cortex as _cx
                        comp = _cx.conviction(scores)["conviction"]
                    except Exception:  # noqa: BLE001
                        comp = 0.0
                else:
                    comp = blend(scores, weights)
                put(book, sym, _position(comp), last)
            if sym == "SPY":
                put("spy_hold", "SPY", 1.0, last)
        except Exception:  # noqa: BLE001
            continue
    c.commit()
    c.close()
    return {"day": day, "written": written, "books": len(BOOKS), "universe": len(universe)}


def _fwd_return(symbol: str, day: str, horizon: int) -> float | None:
    try:
        from .crsp import query as crsp
        bars = crsp.get_prices(symbol, "2024-01-01", None)
        rows = [(b["date"], b["close"]) for b in bars if b.get("close")]
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    idx = next((i for i, (d, _) in enumerate(rows) if d >= day), None)
    if idx is None or idx + horizon >= len(rows):
        return None
    p0, p1 = rows[idx][1], rows[idx + horizon][1]
    if not p0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def resolve() -> dict:
    """Resolve matured signals against realized forward returns."""
    c = _conn()
    rows = c.execute("SELECT * FROM snaps WHERE resolved=0").fetchall()
    done = 0
    for r in rows:
        fwd = _fwd_return(r["symbol"], r["day"], r["horizon"])
        if fwd is None:
            continue
        realized = float(r["position"]) * fwd          # position-adjusted forward return
        c.execute("UPDATE snaps SET resolved=1, realized_pct=? WHERE id=?", (realized, r["id"]))
        done += 1
    c.commit()
    c.close()
    return {"resolved": done}


def standings() -> dict:
    """Per-book forward scoreboard, ranked by average realized return."""
    c = _conn()
    rows = c.execute("SELECT book, position, realized_pct FROM snaps WHERE resolved=1").fetchall()
    open_n = c.execute("SELECT COUNT(*) FROM snaps WHERE resolved=0").fetchone()[0]
    c.close()
    agg: dict[str, dict] = {}
    for r in rows:
        b = agg.setdefault(r["book"], {"n": 0, "wins": 0, "sum": 0.0, "active": 0})
        # spy_hold is always long; score-books may sit out (position 0) -> not a trade
        if r["book"] != "spy_hold" and r["position"] == 0:
            b["n"] += 1
            continue
        b["n"] += 1
        b["active"] += 1
        b["sum"] += float(r["realized_pct"])
        if float(r["realized_pct"]) > 0:
            b["wins"] += 1
    books = []
    for name in BOOKS:
        a = agg.get(name, {"n": 0, "wins": 0, "sum": 0.0, "active": 0})
        act = a["active"]
        books.append({
            "book": name,
            "signals": a["n"],
            "trades": act,
            "total_return_pct": round(a["sum"], 2),
            "avg_return_pct": round(a["sum"] / act, 3) if act else None,
            "hit_rate": round(a["wins"] / act, 3) if act else None,
        })
    # rank by total realized return among books with trades
    ranked = sorted(books, key=lambda x: (x["total_return_pct"] if x["trades"] else -1e9), reverse=True)
    spy = next((b for b in books if b["book"] == "spy_hold"), None)
    leader = next((b for b in ranked if b["trades"]), None)
    return {"books": ranked, "open_signals": open_n, "spy_hold": spy, "leader": leader}


def run() -> dict:
    snap = snapshot()
    res = resolve()
    st = standings()
    lead = st.get("leader")
    summary = ("Shadow Lab maturing — no resolved signals yet." if not lead else
               f"Shadow leader: {lead['book']} ({lead['total_return_pct']:+.2f}% over "
               f"{lead['trades']} trades, {(lead['hit_rate'] or 0)*100:.0f}% hit).")
    try:
        from . import mesh
        mesh.publish("shadow", "standings", summary, salience=0.55)
    except Exception:  # noqa: BLE001
        pass
    return {"snapshot": snap, "resolve": res, "summary": summary,
            "leader": lead, "open_signals": st.get("open_signals", 0)}


def status() -> dict:
    return {"standings": standings(), "universe": UNIVERSE, "horizon": HORIZON,
            "books": list(BOOKS)}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    import json
    print(json.dumps(run(), indent=2)[:1200])
