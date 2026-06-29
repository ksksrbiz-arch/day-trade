"""
No-account news feeds -- per-ticker catalyst discovery, zero signup/keys.

  * Yahoo Finance per-ticker RSS  (https://feeds.finance.yahoo.com/rss/2.0/headline?s=SYM)
  * Google News RSS query         (https://news.google.com/rss/search?q=SYM+stock)
  * SEC EDGAR latest 8-K          (best-effort; material-event filings)

These let the day-trader engine WATCH a specific name's news flow the way a human
trader keeps a ticker's headlines open. Everything fail-soft.
"""
from __future__ import annotations

import hashlib
import urllib.request

import feedparser

_UA = "paper-trader research (contact: skdev@1commercesolutions.com)"


def _id(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8", "ignore")).hexdigest()[:16]


def ticker_news(symbol: str, limit: int = 12) -> list[dict]:
    """Deduped recent headlines for one symbol from no-key sources."""
    sym = symbol.upper()
    items, seen = [], set()
    feeds = [
        ("yahoo", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"),
        ("googlenews", f"https://news.google.com/rss/search?q={sym}+stock&hl=en-US&gl=US&ceid=US:en"),
    ]
    for src, url in feeds:
        try:
            d = feedparser.parse(url)
            for e in d.entries[:limit]:
                title = (e.get("title", "") or "").strip()
                if not title:
                    continue
                key = title.lower()[:80]
                if key in seen:
                    continue
                seen.add(key)
                items.append({"id": _id(title), "title": title,
                              "summary": (e.get("summary", "") or "")[:300],
                              "source": src, "symbol": sym})
        except Exception:
            continue
    return items[:limit]


def sec_8k_latest(limit: int = 20) -> list[dict]:
    """Best-effort latest 8-K material-event filings (no key). May return []."""
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
           f"&type=8-K&output=atom&count={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        raw = urllib.request.urlopen(req, timeout=15).read()
        d = feedparser.parse(raw)
        out = []
        for e in d.entries[:limit]:
            t = (e.get("title", "") or "").strip()
            if t:
                out.append({"id": _id(t), "title": t, "summary": e.get("summary", "")[:200],
                            "source": "sec8k"})
        return out
    except Exception:
        return []
