"""Point-in-time query API over the CRSP-lite master. This is what the
walk-forward backtester calls so it trades the universe *as it existed* on each
historical date -- including names that were later delisted -- instead of
today's survivors.

Prices are fetched on demand from Tiingo (which retains delisted tickers) and
cached into the local DB, so repeated backtests are fast and offline.
"""
from __future__ import annotations
from datetime import datetime

from . import sources
from .schema import connect, now_iso


# --------------------------------------------------------------------------- #
# Membership / identity                                                        #
# --------------------------------------------------------------------------- #
def constituents_asof(on: str, index_name: str = "SP500", conn=None) -> list[str]:
    """Tickers that were index members on ISO date `on` (point-in-time)."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute(
        "SELECT ticker FROM membership WHERE index_name=? "
        "AND (start_date IS NULL OR start_date<=?) "
        "AND (end_date IS NULL OR end_date>?)",
        (index_name, on, on)).fetchall()
    if own:
        conn.close()
    return sorted({r["ticker"] for r in rows})


def permno_for(ticker: str, conn=None):
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT permno FROM securities WHERE ticker=? LIMIT 1",
                     (ticker,)).fetchone()
    if own:
        conn.close()
    return r["permno"] if r else None


def delist_reason(ticker: str, conn=None):
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT reason_class,ai_confidence,delist_date FROM delistings "
                     "WHERE ticker=? AND reason_class IS NOT NULL "
                     "ORDER BY ai_confidence DESC LIMIT 1", (ticker,)).fetchone()
    if own:
        conn.close()
    return dict(r) if r else None


# --------------------------------------------------------------------------- #
# Prices (on-demand fetch + cache)                                             #
# --------------------------------------------------------------------------- #
def get_prices(ticker: str, start: str = "1990-01-01", end: str | None = None,
               fetch: bool = True, conn=None) -> list[dict]:
    """Return cached daily bars; if missing and fetch=True, pull from Tiingo
    (retains delisted names) and cache. Returns [{date,open,high,low,close,adj_close,volume}]."""
    own = conn is None
    conn = conn or connect()
    pn = permno_for(ticker, conn)
    if pn is None:
        # unknown to master: create a minimal security so prices have a home
        conn.execute("INSERT INTO securities(ticker,status,first_seen,last_seen,created_at)"
                     " VALUES(?,?,?,?,?)", (ticker, "unknown", now_iso(), now_iso(), now_iso()))
        conn.commit()
        pn = conn.execute("SELECT permno FROM securities WHERE ticker=? ORDER BY permno DESC LIMIT 1",
                          (ticker,)).fetchone()["permno"]

    cached = conn.execute(
        "SELECT date,open,high,low,close,adj_close,volume FROM prices "
        "WHERE permno=? AND date>=? " + ("AND date<=? " if end else "") +
        "ORDER BY date", (pn, start, end) if end else (pn, start)).fetchall()
    if cached or not fetch:
        out = [dict(r) for r in cached]
        if own:
            conn.close()
        return out

    bars, note = sources.tiingo_prices(ticker, start=start, end=end)
    for b in bars:
        conn.execute(
            "INSERT OR IGNORE INTO prices(permno,date,open,high,low,close,adj_close,volume,source)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (pn, b["date"], b["open"], b["high"], b["low"], b["close"],
             b["adj_close"], b["volume"], "tiingo"))
    conn.commit()
    out = [b for b in bars]
    if own:
        conn.close()
    return out


def backfill_universe(on: str, start: str, end: str, index_name="SP500",
                      limit: int | None = None, verbose=True):
    """Cache prices for every member as-of `on` (incl. delisted) over [start,end].
    Use before a walk-forward run so the backtest is fully offline + bias-reduced."""
    conn = connect()
    tickers = constituents_asof(on, index_name, conn)
    if limit:
        tickers = tickers[:limit]
    ok = miss = 0
    for i, t in enumerate(tickers, 1):
        bars = get_prices(t, start, end, fetch=True, conn=conn)
        if bars:
            ok += 1
        else:
            miss += 1
        if verbose and i % 25 == 0:
            print(f"  {i}/{len(tickers)} cached (ok={ok} miss={miss})")
    conn.close()
    return {"universe": len(tickers), "with_prices": ok, "missing": miss}


# --------------------------------------------------------------------------- #
# Audit                                                                        #
# --------------------------------------------------------------------------- #
def survivorship_audit(conn=None) -> dict:
    own = conn is None
    conn = conn or connect()
    g = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
    today = datetime.now().date().isoformat()
    out = {
        "members_today": len(constituents_asof(today, conn=conn)),
        "members_2010": len(constituents_asof("2010-01-04", conn=conn)),
        "members_2008_crisis": len(constituents_asof("2008-09-15", conn=conn)),
        "total_intervals": g("SELECT COUNT(*) FROM membership"),
        "removed_names_recovered": g("SELECT COUNT(*) FROM membership WHERE end_date IS NOT NULL"),
        "delistings_total": g("SELECT COUNT(*) FROM delistings"),
        "delistings_ai_classified": g("SELECT COUNT(*) FROM delistings WHERE reason_class IS NOT NULL"),
        "securities_total": g("SELECT COUNT(*) FROM securities"),
        "prices_cached": g("SELECT COUNT(*) FROM prices"),
    }
    if own:
        conn.close()
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(survivorship_audit(), indent=2))
    # prove we can price a name that left the index (point-in-time, delisted ok)
    for t in ("AAPL", "LEH", "ETFC", "RX"):
        bars = get_prices(t, "2007-01-01", "2009-12-31")
        print(f"{t:6s} bars={len(bars)}  reason={delist_reason(t)}")
