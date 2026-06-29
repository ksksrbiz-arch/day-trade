"""Reconstruct point-in-time S&P 500 membership from Wikipedia current list +
change log (the Teddy Koker method). This is what removes index survivorship
bias: we recover the companies that were *removed* (acquired / bankrupt /
relegated), not just today's survivors.
"""
from __future__ import annotations
from datetime import date, datetime

_FMTS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%B %d %Y", "%m/%d/%Y")


def _pdate(s: str):
    s = (s or "").strip().replace(" ", " ")
    for f in _FMTS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def reconstruct_intervals(current: list[dict], changes: list[dict]):
    """Return [{ticker,name,start_date,end_date}] membership intervals.

    start_date None  -> member since before the change log begins.
    end_date   None  -> still a member today.
    """
    # Parse + sort changes ascending.
    ch = []
    for c in changes:
        d = _pdate(c.get("date", ""))
        if d:
            ch.append((d, c))
    ch.sort(key=lambda x: x[0])

    cur = {c["ticker"] for c in current if c.get("ticker")}
    names = {c["ticker"]: c.get("name", "") for c in current}

    # Undo every change (newest -> oldest) to recover the set before the log.
    init = set(cur)
    for d, c in reversed(ch):
        add, rem = c.get("added_ticker"), c.get("removed_ticker")
        if add:
            init.discard(add)            # undo an addition
        if rem:
            init.add(rem)                # undo a removal
            if c.get("removed_name"):
                names.setdefault(rem, c["removed_name"])

    # Replay forward, recording intervals.
    open_start = {t: None for t in init}   # ticker -> start_date (None=pre-log)
    closed = []                            # finished intervals
    for d, c in ch:
        rem, add = c.get("removed_ticker"), c.get("added_ticker")
        if rem and rem in open_start:
            closed.append({"ticker": rem, "name": names.get(rem, c.get("removed_name", "")),
                           "start_date": open_start.pop(rem), "end_date": d.isoformat()})
        elif rem:  # removed but never seen open -> single-day artifact, record anyway
            closed.append({"ticker": rem, "name": c.get("removed_name", ""),
                           "start_date": None, "end_date": d.isoformat()})
        if add:
            open_start[add] = d.isoformat()
            if c.get("added_name"):
                names[add] = c["added_name"]

    # Open intervals: a live current member keeps end_date None; an open
    # interval whose ticker is NOT in the live set means we missed its removal
    # in the log -- close it at the last change date so "today" reconciles.
    last_d = ch[-1][0].isoformat() if ch else None
    for t, s in open_start.items():
        end = None if t in cur else last_d
        closed.append({"ticker": t, "name": names.get(t, ""),
                       "start_date": s, "end_date": end})
    return closed


def constituents_asof(intervals: list[dict], on: str):
    """Tickers that were S&P 500 members on ISO date `on`."""
    d = _pdate(on) or datetime.strptime(on[:10], "%Y-%m-%d").date()
    out = []
    for iv in intervals:
        s = iv["start_date"]
        e = iv["end_date"]
        sd = datetime.strptime(s, "%Y-%m-%d").date() if s else date.min
        ed = datetime.strptime(e, "%Y-%m-%d").date() if e else date.max
        if sd <= d < ed:
            out.append(iv["ticker"])
    return sorted(set(out))


if __name__ == "__main__":
    from . import sources
    cur, _ = sources.sp500_current()
    chg, _ = sources.sp500_changes()
    iv = reconstruct_intervals(cur, chg)
    print("intervals:", len(iv))
    print("members today:", len(constituents_asof(iv, datetime.now().date().isoformat())))
    print("members 2008-09-15:", len(constituents_asof(iv, "2008-09-15")))
    # spot-check: Lehman should appear as a removed (delisted) name historically
    dead = [x for x in iv if x["end_date"]]
    print("historically-removed names:", len(dead), "e.g.", [d["ticker"] for d in dead[:8]])
