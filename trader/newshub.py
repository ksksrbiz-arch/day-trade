"""Unified news aggregator -- one feed from every source the platform watches.

Pulls and fuses ALL of the desk's news streams into a single, deduped,
sentiment-tagged, symbol-tagged, ranked stream:

  markets   -- configured market RSS (WSJ, etc.)        cfg.feeds
  ticker    -- per-name Yahoo Finance + Google News     newsfeeds.ticker_news
  filing    -- SEC EDGAR latest 8-K material events      newsfeeds.sec_8k_latest
  social    -- WallStreetBets buzz                       wsb.buzz
  broker    -- Clear Street headlines (if enabled)

Each item is normalized to {id, ts, source, category, title, summary, link,
symbols[], sentiment}. A fast finance lexicon scores sentiment (no network, so
it can run on every refresh); items are deduped by headline and ranked by
recency x salience x source weight. `market_sentiment()` and `catalysts()` feed
the reasoning/contextual layer (awareness + mesh). Everything fail-soft + cached.
"""
from __future__ import annotations

import hashlib
import re
import time

# ---- source weights for ranking ---- #
_SRC_W = {"filing": 1.0, "markets": 0.7, "broker": 0.8, "ticker": 0.6, "social": 0.4}
_CACHE = {"ts": 0.0, "key": None, "data": None}
TTL = 120.0

_POS = {"beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally",
        "rallies", "record", "upgrade", "upgraded", "raises", "raised", "tops", "strong",
        "gain", "gains", "wins", "win", "approval", "approved", "breakthrough", "outperform",
        "bullish", "rebound", "boost", "boosts", "profit", "growth", "high", "highs"}
_NEG = {"miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls", "sink",
        "sinks", "downgrade", "downgraded", "cut", "cuts", "lawsuit", "probe", "fraud",
        "warning", "warns", "slump", "weak", "loss", "losses", "recall", "halt", "halts",
        "bankruptcy", "investigation", "selloff", "tumble", "tumbles", "bearish", "crash",
        "slashes", "plummet", "plummets", "default", "layoffs", "downturn", "low", "lows"}

_TICK_RE = re.compile(r"\$([A-Z]{1,5})\b")
_WORD_RE = re.compile(r"[A-Za-z']+")


def _id(t: str) -> str:
    return hashlib.sha1((t or "").encode("utf-8", "ignore")).hexdigest()[:16]


def lex_sentiment(text: str) -> float:
    """Fast finance-lexicon sentiment in [-1,1]; deterministic, no network."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in _POS)
    neg = sum(1 for w in words if w in _NEG)
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


def extract_symbols(text: str, universe: set | None = None) -> list[str]:
    """Find $TICK cashtags, plus uppercase tokens that match a known universe."""
    syms = set(_TICK_RE.findall(text or ""))
    if universe:
        for tok in re.findall(r"\b([A-Z]{2,5})\b", text or ""):
            if tok in universe:
                syms.add(tok)
    return sorted(syms)


def _norm(title, summary, link, source, category, ts=None, symbols=None):
    title = (title or "").strip()
    return {"id": _id(title or link or str(ts)), "ts": float(ts or time.time()),
            "source": source, "category": category, "title": title,
            "summary": (summary or "")[:280], "link": link or "",
            "symbols": list(symbols or []),
            "sentiment": lex_sentiment(title + " " + (summary or ""))}


def _dedupe(items: list[dict]) -> list[dict]:
    """Collapse by headline; merge symbols, keep the highest-weighted source."""
    by_key: dict[str, dict] = {}
    for it in items:
        key = (it["title"] or it["link"]).lower()[:90]
        if not key:
            continue
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = it
        else:
            cur["symbols"] = sorted(set(cur["symbols"]) | set(it["symbols"]))
            if _SRC_W.get(it["category"], 0) > _SRC_W.get(cur["category"], 0):
                it["symbols"] = cur["symbols"]
                by_key[key] = it
    return list(by_key.values())


def _rank(items: list[dict]) -> list[dict]:
    now = time.time()
    def score(it):
        age_h = max(0.0, (now - it["ts"]) / 3600.0)
        recency = 1.0 / (1.0 + age_h / 12.0)          # ~12h half-ish decay
        return recency * (1.0 + _SRC_W.get(it["category"], 0.5)) * (1.0 + abs(it["sentiment"]))
    return sorted(items, key=score, reverse=True)


def market_sentiment(items: list[dict] | None = None) -> dict:
    items = items if items is not None else aggregate().get("items", [])
    rel = [it for it in items if it["category"] in ("markets", "ticker", "broker")]
    if not rel:
        return {"net": 0.0, "pos": 0, "neg": 0, "n": 0, "label": "neutral"}
    vals = [it["sentiment"] for it in rel]
    net = round(sum(vals) / len(vals), 3)
    pos = sum(1 for v in vals if v > 0.05)
    neg = sum(1 for v in vals if v < -0.05)
    label = "risk-on" if net > 0.08 else "risk-off" if net < -0.08 else "mixed"
    return {"net": net, "pos": pos, "neg": neg, "n": len(rel), "label": label}


def catalysts(symbol: str, items: list[dict] | None = None, k: int = 6) -> list[dict]:
    symbol = symbol.upper()
    items = items if items is not None else aggregate(symbols=[symbol]).get("items", [])
    hits = [it for it in items if symbol in it["symbols"]]
    return _rank(hits)[:k]


def _universe(symbols=None) -> list[str]:
    if symbols:
        return [s.upper() for s in symbols][:8]
    out, seen = [], set()
    try:
        from . import config
        for s in (getattr(config.load(), "universe", []) or []):
            s = str(s).upper()
            if s not in seen:
                seen.add(s); out.append(s)
    except Exception:  # noqa: BLE001
        pass
    for s in ["SPY", "QQQ", "NVDA", "AAPL", "TSLA", "MSFT"]:
        if s not in seen:
            seen.add(s); out.append(s)
    return out[:6]


def _entry_ts(e) -> float:
    for k in ("published_parsed", "updated_parsed"):
        v = e.get(k) if hasattr(e, "get") else None
        if v:
            try:
                return time.mktime(v)
            except Exception:  # noqa: BLE001
                pass
    return time.time()


def aggregate(symbols=None, limit: int = 80) -> dict:
    key = ",".join(symbols) if symbols else "_default"
    now = time.time()
    if _CACHE["data"] and _CACHE["key"] == key and now - _CACHE["ts"] < TTL:
        return _CACHE["data"]

    uni = _universe(symbols)
    uniset = set(uni)
    items: list[dict] = []

    # markets RSS (configured feeds)
    try:
        import feedparser
        from . import config
        for url in (getattr(config.load(), "feeds", []) or [])[:8]:
            try:
                d = feedparser.parse(url)
                src = (d.feed.get("title", "RSS") if hasattr(d, "feed") else "RSS")[:40]
                for e in d.entries[:12]:
                    items.append(_norm(e.get("title"), e.get("summary"), e.get("link"),
                                       src, "markets", _entry_ts(e),
                                       extract_symbols(e.get("title", ""), uniset)))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    # per-ticker (Yahoo + Google)
    try:
        from . import newsfeeds
        for sym in uni:
            for it in newsfeeds.ticker_news(sym, 6):
                items.append(_norm(it.get("title"), it.get("summary"), it.get("link", ""),
                                   it.get("source", "ticker"), "ticker", None, [sym]))
    except Exception:  # noqa: BLE001
        pass

    # SEC 8-K filings
    try:
        from . import newsfeeds
        for it in newsfeeds.sec_8k_latest(15):
            items.append(_norm(it.get("title"), it.get("summary"), it.get("link", ""),
                               "SEC 8-K", "filing", None, extract_symbols(it.get("title", ""), uniset)))
    except Exception:  # noqa: BLE001
        pass

    # WSB social buzz
    try:
        from . import wsb
        bz = wsb.buzz() or {}
        for p in (bz.get("posts") or [])[:12]:
            t = p.get("title") or p.get("text") or ""
            items.append(_norm(t, p.get("summary", ""), p.get("link", p.get("url", "")),
                               "WallStreetBets", "social", None,
                               extract_symbols(t, uniset)))
        if not bz.get("posts"):
            for tk in (bz.get("tickers") or [])[:8]:
                items.append(_norm(f"WSB buzz: {tk.get('symbol')} ({tk.get('mentions')} mentions)",
                                   "", "", "WallStreetBets", "social", None, [tk.get("symbol", "")]))
    except Exception:  # noqa: BLE001
        pass

    # Clear Street (optional)
    try:
        from . import config
        c = config.load()
        if getattr(c, "use_clearstreet", False):
            from .broker_clearstreet import ClearStreetClient  # type: ignore
            cs = ClearStreetClient(c.cs_client_id, c.cs_client_secret, c.cs_audience, c.cs_base_url)
            for n in cs.news(limit=12):
                items.append(_norm(n.get("title"), n.get("summary", ""), "", "Clear Street",
                                   "broker", None, []))
    except Exception:  # noqa: BLE001
        pass

    items = _rank(_dedupe(items))[:limit]
    data = {"items": items, "market_sentiment": market_sentiment(items),
            "counts": _counts(items), "universe": uni,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _CACHE.update(ts=now, key=key, data=data)
    return data


def _counts(items):
    c = {}
    for it in items:
        c[it["category"]] = c.get(it["category"], 0) + 1
    return c


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    import json
    d = aggregate()
    print("items:", len(d["items"]), "counts:", d["counts"], "sentiment:", d["market_sentiment"])
    for it in d["items"][:8]:
        print(f"  [{it['category']}] {it['sentiment']:+.2f} {it['title'][:70]}")
