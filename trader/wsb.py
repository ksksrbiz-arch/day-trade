"""WallStreetBets RSS cross-reference.

Pulls the r/wallstreetbets RSS feed, extracts the tickers being talked about,
and (optionally) scores their sentiment via Cloudflare. The agents use this as
a *social-buzz* signal to cross-reference against price/quant/fundamental
evidence -- retail attention often precedes volatility, which is tradeable
information regardless of whether the crowd is right.

Pure-stdlib feed parsing (no hard feedparser dependency); fail-soft.
"""
from __future__ import annotations

import html
import os
import re
import time
import urllib.request

DEFAULT_URL = os.environ.get(
    "WSB_RSS_URL", "https://rss.app/feeds/S9iXtNCjhNAk5trl.xml")
_UA = "Mozilla/5.0 (paper-trader wsb)"
_cache: dict[str, tuple] = {}

# words that look like tickers but aren't
_STOP = {"A", "I", "DD", "YOLO", "CEO", "CFO", "IPO", "ATH", "FD", "FOMO", "WSB",
         "USA", "USD", "EPS", "ER", "PT", "AH", "PM", "EOD", "IV", "OTM", "ITM",
         "ETF", "SEC", "FED", "GDP", "CPI", "AI", "EV", "TA", "RH", "PR", "Q1",
         "Q2", "Q3", "Q4", "US", "IT", "TO", "ON", "IN", "OR", "BE", "GO", "UP",
         "THE", "AND", "FOR", "ARE", "BUY", "ALL", "NOW", "NEW", "CAN", "GET",
         "OG", "IMO", "TLDR", "EU", "UK", "LOL", "WTF", "GG", "RIP", "LFG",
         "LMAO", "IM", "CALL", "CALLS", "PUT", "PUTS", "MOON", "HODL", "HOLD",
         "SELL", "GAIN", "LOSS", "BTW", "EDIT", "DCA", "WEN" if False else "ZZZ"}

_TAG = re.compile(r"<[^>]+>")


def _strip(t: str) -> str:
    return html.unescape(_TAG.sub("", t or "")).strip()


def _item_blocks(xml: str):
    return re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)


def _field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    if not m:
        return ""
    v = m.group(1)
    cd = re.search(r"<!\[CDATA\[(.*?)\]\]>", v, re.DOTALL)
    return _strip(cd.group(1) if cd else v)


def fetch_items(url: str | None = None, limit: int = 30, ttl: float = 300):
    url = url or DEFAULT_URL
    now = time.time()
    if url in _cache and now - _cache[url][0] < ttl:
        return _cache[url][1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return [{"title": f"[wsb feed error: {str(e)[:80]}]", "link": "", "ts": ""}]
    items = []
    for b in _item_blocks(xml)[:limit]:
        items.append({"title": _field(b, "title"),
                      "link": _field(b, "link"),
                      "ts": _field(b, "pubDate"),
                      "summary": _field(b, "description")[:200]})
    _cache[url] = (now, items)
    return items


_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_UPPER = re.compile(r"\b([A-Z]{2,5})\b")


def _valid_tickers() -> set[str]:
    """Validate candidate symbols against the CRSP-lite security master."""
    try:
        from .crsp.schema import connect
        c = connect()
        rows = c.execute("SELECT DISTINCT ticker FROM securities WHERE ticker IS NOT NULL").fetchall()
        c.close()
        return {r[0] for r in rows if r[0]}
    except Exception:  # noqa: BLE001
        return set()


def extract_tickers(text: str, valid: set[str] | None = None) -> list[str]:
    found = []
    for m in _CASHTAG.findall(text or ""):
        found.append(m.upper())
    if valid:
        for m in _UPPER.findall(text or ""):
            if m in valid and m not in _STOP:
                found.append(m)
    return found


def buzz(url: str | None = None, top: int = 12) -> dict:
    """Return {tickers:[{symbol,mentions}], n_items, items:[...]}. """
    items = fetch_items(url)
    valid = _valid_tickers()
    counts: dict[str, int] = {}
    for it in items:
        for tk in extract_tickers(it["title"] + " " + it.get("summary", ""), valid):
            counts[tk] = counts.get(tk, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return {"n_items": len(items),
            "tickers": [{"symbol": s, "mentions": n} for s, n in ranked],
            "items": items[:15]}


def cross_reference(symbol: str, url: str | None = None) -> dict:
    """Buzz + Cloudflare sentiment for one symbol from WSB chatter."""
    symbol = symbol.upper()
    items = fetch_items(url)
    hits = [it for it in items
            if re.search(rf"(\${symbol}\b|\b{symbol}\b)", it["title"] + " " + it.get("summary", ""))]
    sent = 0.0
    if hits:
        try:
            from .agents import cloudflare as cf
            if cf.available():
                scores = [cf.sentiment(h["title"]) for h in hits[:5]]
                sent = round(sum(scores) / len(scores), 3) if scores else 0.0
        except Exception:  # noqa: BLE001
            pass
    return {"symbol": symbol, "mentions": len(hits), "wsb_sentiment": sent,
            "titles": [h["title"][:80] for h in hits[:3]]}


if __name__ == "__main__":
    b = buzz()
    print("items:", b["n_items"])
    print("top buzz:", b["tickers"][:8])
    for it in b["items"][:3]:
        print(" -", it["title"][:70])
    if b["tickers"]:
        print("xref:", cross_reference(b["tickers"][0]["symbol"]))
