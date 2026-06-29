"""Builder pipeline: fuse the 5 sources into the security master, assign
permanent ids (PERMNO), reconstruct PIT membership, record delistings, and log
cross-source reconciliation. Idempotent: re-running keeps permnos stable
(matched by CIK then ticker) and rebuilds derived tables.
"""
from __future__ import annotations
import json
from . import sources
from .schema import init_db, now_iso, log_run
from .membership import reconstruct_intervals


def _norm_name(s: str) -> str:
    s = (s or "").upper()
    for junk in (" INC", " CORP", " CO", " LTD", " PLC", " THE", ".", ",", "'"):
        s = s.replace(junk, "")
    return " ".join(s.split())


def build_master(conn=None, verbose=True):
    conn = conn or init_db()
    c = conn.cursor()

    # ---- pull sources -----------------------------------------------------
    sec, n1 = sources.sec_company_tickers()
    av_act, n2 = sources.alphavantage_listing("active")
    av_del, n3 = sources.alphavantage_listing("delisted")
    wiki_cur, n4 = sources.sp500_current()
    wiki_chg, n5 = sources.sp500_changes()
    for src, rows, note in [("sec", sec, n1), ("av_active", av_act, n2),
                            ("av_delisted", av_del, n3), ("wiki_current", wiki_cur, n4),
                            ("wiki_changes", wiki_chg, n5)]:
        log_run(conn, src, len(rows), bool(rows), note)
        if verbose:
            print(f"  {src:14s} {len(rows):6d}  {note}")

    sec_by_ticker = {r["ticker"]: r for r in sec}
    intervals = reconstruct_intervals(wiki_cur, wiki_chg)
    wiki_names = {r["ticker"]: r.get("name", "") for r in wiki_cur}
    wiki_sector = {r["ticker"]: r.get("sector", "") for r in wiki_cur}

    # ---- existing permno maps (stability across rebuilds) -----------------
    tkr2permno, cik2permno = {}, {}
    for row in c.execute("SELECT permno,ticker,cik FROM securities"):
        if row["ticker"]:
            tkr2permno[row["ticker"]] = row["permno"]
        if row["cik"]:
            cik2permno[row["cik"]] = row["permno"]

    def resolve_or_create(ticker, cik, name, exch, atype, ipo, delist, status):
        pn = None
        if cik and cik in cik2permno:
            pn = cik2permno[cik]
        elif ticker in tkr2permno:
            pn = tkr2permno[ticker]
        if pn is None:
            c.execute(
                "INSERT INTO securities(cik,ticker,name,exchange,asset_type,"
                "ipo_date,delist_date,status,first_seen,last_seen,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (cik, ticker, name, exch, atype, ipo, delist, status,
                 now_iso(), now_iso(), now_iso()))
            pn = c.lastrowid
        else:
            c.execute(
                "UPDATE securities SET cik=COALESCE(NULLIF(?,''),cik),"
                "name=COALESCE(NULLIF(?,''),name),exchange=COALESCE(NULLIF(?,''),exchange),"
                "asset_type=COALESCE(NULLIF(?,''),asset_type),ipo_date=COALESCE(NULLIF(?,''),ipo_date),"
                "delist_date=COALESCE(NULLIF(?,''),delist_date),status=?,last_seen=? WHERE permno=?",
                (cik, name, exch, atype, ipo, delist, status, now_iso(), pn))
        if ticker:
            tkr2permno[ticker] = pn
        if cik:
            cik2permno[cik] = pn
        return pn

    # ---- universe = AV(active+delisted) U wiki membership tickers ----------
    universe = {}
    for r in av_act:
        universe[r["symbol"]] = dict(r, status="active")
    for r in av_del:
        universe.setdefault(r["symbol"], dict(r, status="delisted"))
    for iv in intervals:
        universe.setdefault(iv["ticker"], {
            "symbol": iv["ticker"], "name": iv.get("name", ""), "exchange": "",
            "asset_type": "Stock", "ipo_date": "", "delist_date": "",
            "status": "delisted" if iv["end_date"] else "active"})

    created = 0
    for t, r in universe.items():
        srec = sec_by_ticker.get(t, {})
        cik = srec.get("cik", "")
        name = r.get("name") or srec.get("name") or wiki_names.get(t, "")
        before = len(tkr2permno)
        pn = resolve_or_create(
            t, cik, name, r.get("exchange", ""), r.get("asset_type", ""),
            r.get("ipo_date", ""), r.get("delist_date", ""), r.get("status", "active"))
        if len(tkr2permno) > before:
            created += 1
        # reconciliation: name agreement across sources
        srcs = {k: v for k, v in {
            "sec": _norm_name(srec.get("name", "")),
            "av": _norm_name(r.get("name", "")),
            "wiki": _norm_name(wiki_names.get(t, "")),
        }.items() if v}
        if len(srcs) >= 2:
            agreed = len(set(srcs.values())) == 1
            c.execute("INSERT INTO reconciliation(permno,field,sources,agreed,chosen,"
                      "confidence,created_at) VALUES(?,?,?,?,?,?,?)",
                      (pn, "name", json.dumps(srcs), int(agreed), name,
                       1.0 if agreed else round(1.0 / len(set(srcs.values())), 2),
                       now_iso()))
    conn.commit()

    # ---- rebuild derived membership table ---------------------------------
    c.execute("DELETE FROM membership")
    for iv in intervals:
        pn = tkr2permno.get(iv["ticker"])
        if pn is None:
            pn = resolve_or_create(iv["ticker"], sec_by_ticker.get(iv["ticker"], {}).get("cik", ""),
                                   iv.get("name", ""), "", "Stock", "", "",
                                   "delisted" if iv["end_date"] else "active")
        c.execute("INSERT INTO membership(permno,ticker,index_name,start_date,end_date,source)"
                  " VALUES(?,?,?,?,?,?)",
                  (pn, iv["ticker"], "SP500", iv["start_date"], iv["end_date"], "wikipedia"))
        if wiki_sector.get(iv["ticker"]):
            c.execute("INSERT INTO enrichment(permno,key,value,confidence,model,created_at)"
                      " VALUES(?,?,?,?,?,?)",
                      (pn, "gics_sector", wiki_sector[iv["ticker"]], 1.0, "wikipedia", now_iso()))

    # ---- rebuild delistings from AV + wiki change reasons -----------------
    c.execute("DELETE FROM delistings")
    for r in av_del:
        pn = tkr2permno.get(r["symbol"])
        if pn and r.get("delist_date"):
            c.execute("INSERT INTO delistings(permno,ticker,delist_date,reason_raw,"
                      "reason_class,ai_confidence,source) VALUES(?,?,?,?,?,?,?)",
                      (pn, r["symbol"], r["delist_date"], "", None, None, "alphavantage"))
    for ch in wiki_chg:
        rt = ch.get("removed_ticker")
        if rt and ch.get("reason"):
            pn = tkr2permno.get(rt)
            if pn:
                c.execute("INSERT INTO delistings(permno,ticker,delist_date,reason_raw,"
                          "reason_class,ai_confidence,source) VALUES(?,?,?,?,?,?,?)",
                          (pn, rt, ch.get("date", ""), ch["reason"][:400], None, None, "wikipedia"))
    conn.commit()

    stats = master_stats(conn)
    if verbose:
        print(f"  -> securities={stats['securities']} (+{created} new), "
              f"membership={stats['membership']}, delistings={stats['delistings']}, "
              f"recovered_removed={stats['removed_names']}")
    return stats


def master_stats(conn):
    c = conn.cursor()
    g = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
    return {
        "securities": g("SELECT COUNT(*) FROM securities"),
        "delisted_secs": g("SELECT COUNT(*) FROM securities WHERE status='delisted'"),
        "membership": g("SELECT COUNT(*) FROM membership"),
        "removed_names": g("SELECT COUNT(*) FROM membership WHERE end_date IS NOT NULL"),
        "delistings": g("SELECT COUNT(*) FROM delistings"),
        "prices": g("SELECT COUNT(*) FROM prices"),
        "enrichment": g("SELECT COUNT(*) FROM enrichment"),
        "reconciliation": g("SELECT COUNT(*) FROM reconciliation"),
    }


if __name__ == "__main__":
    conn = init_db()
    print("Building CRSP-lite security master...")
    build_master(conn)
