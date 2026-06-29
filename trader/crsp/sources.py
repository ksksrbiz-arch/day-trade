"""The five free data sources, each normalized and fail-soft.

Every fetcher returns (rows, note). On any error it returns ([], "<reason>")
so the pipeline degrades gracefully instead of crashing -- consistent with the
rest of the trading system.
"""
from __future__ import annotations
import csv
import io
import json
import os
import urllib.request
import urllib.error
from html.parser import HTMLParser

UA = "Mozilla/5.0 (paper-trader CRSP-lite research; contact: skdev@1commercesolutions.com)"
TIMEOUT = 30


def _get(url: str, headers: dict | None = None, timeout: int = TIMEOUT) -> bytes:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------- #
# 1. Wikipedia -> point-in-time S&P 500 membership                            #
# --------------------------------------------------------------------------- #
class _TableParser(HTMLParser):
    """Minimal wikitable extractor (no pandas/lxml dependency)."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._cur: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            cls = a.get("class", "")
            if "wikitable" in cls:
                self._cur = []
                self._depth = 1
            elif self._cur is not None:
                self._depth += 1
        elif self._cur is not None and tag == "tr" and self._depth == 1:
            self._row = []
        elif self._cur is not None and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._cur is not None:
            self._depth -= 1
            if self._depth == 0:
                self.tables.append(self._cur)
                self._cur = None
        elif tag == "tr" and self._row is not None:
            self._cur.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def sp500_current(timeout: int = TIMEOUT):
    """Current S&P 500 constituents -> [{ticker,name,sector,cik}]."""
    try:
        html = _get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            timeout=timeout,
        ).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return [], f"wiki current fetch failed: {e}"
    p = _TableParser()
    p.feed(html)
    for tbl in p.tables:
        if not tbl:
            continue
        hdr = [c.lower() for c in tbl[0]]
        if any("symbol" in c for c in hdr) and any("security" in c for c in hdr):
            sym_i = next(i for i, c in enumerate(hdr) if "symbol" in c)
            nm_i = next(i for i, c in enumerate(hdr) if "security" in c)
            sec_i = next((i for i, c in enumerate(hdr) if "sector" in c), None)
            cik_i = next((i for i, c in enumerate(hdr) if "cik" in c), None)
            rows = []
            for r in tbl[1:]:
                if len(r) <= sym_i:
                    continue
                t = r[sym_i].replace(".", "-").strip()
                if not t:
                    continue
                rows.append({
                    "ticker": t,
                    "name": r[nm_i] if len(r) > nm_i else "",
                    "sector": r[sec_i] if sec_i is not None and len(r) > sec_i else "",
                    "cik": r[cik_i].zfill(10) if cik_i is not None and len(r) > cik_i and r[cik_i].isdigit() else "",
                })
            return rows, f"wiki current ok ({len(rows)})"
    return [], "wiki current: constituents table not found"


def sp500_changes(timeout: int = TIMEOUT):
    """S&P 500 add/remove change log -> [{date,added,removed,reason}]."""
    try:
        html = _get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            timeout=timeout,
        ).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return [], f"wiki changes fetch failed: {e}"
    p = _TableParser()
    p.feed(html)
    for tbl in p.tables:
        if len(tbl) < 2:
            continue
        flat = " ".join(c.lower() for c in tbl[0])
        if "added" in flat and "removed" in flat:
            # Header is usually two rows: [Date, Added, Added, Removed, Removed, Reason]
            rows = []
            for r in tbl[1:]:
                if len(r) < 5:
                    continue
                date = r[0].strip()
                if not date or date.lower().startswith("date"):
                    continue
                rows.append({
                    "date": date,
                    "added_ticker": r[1].replace(".", "-").strip(),
                    "added_name": r[2].strip() if len(r) > 2 else "",
                    "removed_ticker": r[3].replace(".", "-").strip() if len(r) > 3 else "",
                    "removed_name": r[4].strip() if len(r) > 4 else "",
                    "reason": r[5].strip() if len(r) > 5 else "",
                })
            return rows, f"wiki changes ok ({len(rows)})"
    return [], "wiki changes: changes table not found"


# --------------------------------------------------------------------------- #
# 2. Alpha Vantage LISTING_STATUS -> active + delisted universe               #
# --------------------------------------------------------------------------- #
def alphavantage_listing(state: str = "active", date: str | None = None,
                         timeout: int = TIMEOUT):
    """state in {active, delisted}. Optional date=YYYY-MM-DD for a PIT snapshot.
    Returns [{symbol,name,exchange,asset_type,ipo_date,delist_date,status}]."""
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not key:
        return [], "alphavantage: no key"
    url = (f"https://www.alphavantage.co/query?function=LISTING_STATUS"
           f"&state={state}&apikey={key}")
    if date:
        url += f"&date={date}"
    try:
        raw = _get(url, timeout=timeout).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return [], f"alphavantage fetch failed: {e}"
    if raw.lstrip().startswith("{"):  # rate-limit / error JSON
        return [], f"alphavantage non-csv: {raw[:160]}"
    rows = []
    rd = csv.DictReader(io.StringIO(raw))
    for r in rd:
        sym = (r.get("symbol") or "").replace(".", "-").strip()
        if not sym:
            continue
        rows.append({
            "symbol": sym,
            "name": (r.get("name") or "").strip(),
            "exchange": (r.get("exchange") or "").strip(),
            "asset_type": (r.get("assetType") or "").strip(),
            "ipo_date": (r.get("ipoDate") or "").strip(),
            "delist_date": (r.get("delistingDate") or "").strip().replace("null", ""),
            "status": (r.get("status") or state).strip(),
        })
    return rows, f"alphavantage {state} ok ({len(rows)})"


# --------------------------------------------------------------------------- #
# 3. SEC EDGAR -> CIK permanent identity                                       #
# --------------------------------------------------------------------------- #
def sec_company_tickers(timeout: int = TIMEOUT):
    """SEC ticker<->CIK<->name map -> [{ticker,cik,name}]. CIK is the anchor."""
    try:
        raw = _get("https://www.sec.gov/files/company_tickers.json",
                   timeout=timeout)
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return [], f"sec fetch failed: {e}"
    rows = []
    items = data.values() if isinstance(data, dict) else data
    for it in items:
        t = str(it.get("ticker", "")).replace(".", "-").strip()
        if not t:
            continue
        rows.append({
            "ticker": t,
            "cik": str(it.get("cik_str", "")).zfill(10),
            "name": (it.get("title") or "").strip(),
        })
    return rows, f"sec ok ({len(rows)})"


# --------------------------------------------------------------------------- #
# 4. Tiingo -> daily prices (retains delisted tickers)                         #
# --------------------------------------------------------------------------- #
def tiingo_meta(ticker: str, timeout: int = TIMEOUT):
    key = os.environ.get("TIINGO_TOKEN", "")
    if not key:
        return {}, "tiingo: no key"
    try:
        raw = _get(f"https://api.tiingo.com/tiingo/daily/{ticker}?token={key}",
                   timeout=timeout)
        return json.loads(raw), "ok"
    except Exception as e:  # noqa: BLE001
        return {}, f"tiingo meta failed: {e}"


def tiingo_prices(ticker: str, start: str = "1990-01-01", end: str | None = None,
                  timeout: int = TIMEOUT):
    """Daily OHLCV (+adjClose) -> [{date,open,high,low,close,adj_close,volume}]."""
    key = os.environ.get("TIINGO_TOKEN", "")
    if not key:
        return [], "tiingo: no key"
    url = (f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
           f"?startDate={start}&token={key}&format=json")
    if end:
        url += f"&endDate={end}"
    try:
        raw = _get(url, timeout=timeout)
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return [], f"tiingo prices failed: {e}"
    rows = []
    for d in data:
        dt = (d.get("date") or "")[:10]
        if not dt:
            continue
        rows.append({
            "date": dt,
            "open": d.get("open"), "high": d.get("high"),
            "low": d.get("low"), "close": d.get("close"),
            "adj_close": d.get("adjClose", d.get("close")),
            "volume": d.get("volume"),
        })
    return rows, f"tiingo {ticker} ok ({len(rows)})"


# --------------------------------------------------------------------------- #
# 5. FMP -> fallback delisted list / prices (optional key)                     #
# --------------------------------------------------------------------------- #
def fmp_delisted(page: int = 0, timeout: int = TIMEOUT):
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        return [], "fmp: no key (optional)"
    url = (f"https://financialmodelingprep.com/api/v3/delisted-companies"
           f"?page={page}&apikey={key}")
    try:
        raw = _get(url, timeout=timeout)
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return [], f"fmp fetch failed: {e}"
    rows = []
    for d in data:
        sym = (d.get("symbol") or "").replace(".", "-").strip()
        if not sym:
            continue
        rows.append({
            "symbol": sym,
            "name": (d.get("companyName") or "").strip(),
            "exchange": (d.get("exchange") or "").strip(),
            "ipo_date": (d.get("ipoDate") or "").strip(),
            "delist_date": (d.get("delistedDate") or "").strip(),
        })
    return rows, f"fmp ok ({len(rows)})"


if __name__ == "__main__":
    for label, fn in [
        ("wiki.current", lambda: sp500_current()),
        ("wiki.changes", lambda: sp500_changes()),
        ("av.delisted", lambda: alphavantage_listing("delisted")),
        ("sec", lambda: sec_company_tickers()),
    ]:
        rows, note = fn()
        print(f"{label:16s} {len(rows):6d}  {note}")
