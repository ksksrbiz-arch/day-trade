"""Tape & Depth (TAS) — recent time & sales prints + top-of-book depth.

Backend for the ``Tape (TAS)`` terminal panel. Two reads:

* **book** — the latest top-of-book quote for a symbol (bid/ask + sizes) with an
  order-flow imbalance from :func:`trader.ofi.ofi`. Sourced first from the live
  :data:`trader.quotestream.hub` cache (free, already streaming); falls back to a
  single latest-quote REST pull when the hub has nothing yet but keys exist.
* **prints** — recent trades (time & sales) via Alpaca's IEX historical feed.

KEYLESS-SAFE by construction: with no Alpaca credentials — or on any upstream
error — this returns ``{"symbol": symbol, "prints": [], "book": {}}`` and never
raises. Every live/Alpaca call is wrapped.
"""
from __future__ import annotations

from fastapi import APIRouter

from trader import ofi as ofi_mod
from trader.quotestream import hub

router = APIRouter(prefix="/api/tape", tags=["tape"])

_PRINT_LIMIT = 25


def _empty(symbol: str) -> dict:
    return {"symbol": symbol, "prints": [], "book": {}}


def _keys() -> tuple[str | None, str | None]:
    """Alpaca credentials, or (None, None) when unconfigured. Never raises."""
    try:
        from trader import config
        cfg = config.load()
        if cfg.alpaca_key and cfg.alpaca_secret:
            return cfg.alpaca_key, cfg.alpaca_secret
    except Exception as e:  # noqa: BLE001
        print(f"[tape] config load failed: {e}")
    return None, None


def _book(snap: dict) -> dict:
    """Shape a raw quote dict into a book with imbalance, or {} if it's empty."""
    bid = float(snap.get("bid", 0) or 0)
    ask = float(snap.get("ask", 0) or 0)
    bid_size = float(snap.get("bid_size", 0) or 0)
    ask_size = float(snap.get("ask_size", 0) or 0)
    if not (bid or ask or bid_size or ask_size):
        return {}
    return {
        "bid": bid,
        "ask": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "imbalance": ofi_mod.ofi(bid_size, ask_size),
    }


def _book_from_snapshot(symbol: str) -> dict:
    """Top-of-book from the live hub cache (keyless-safe; may be empty)."""
    try:
        hub.ensure_started()
        snap = hub.snapshot([symbol]).get(symbol, {})
    except Exception as e:  # noqa: BLE001
        print(f"[tape] hub snapshot failed {symbol}: {e}")
        return {}
    return _book(snap)


def _fetch_book(symbol: str, key: str, secret: str) -> dict:
    """Best-effort single latest-quote pull to seed the book. Never raises."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        from alpaca.data.enums import DataFeed
        client = StockHistoricalDataClient(key, secret)
        r = client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))
        q = r[symbol]
        return _book({
            "bid": getattr(q, "bid_price", 0),
            "ask": getattr(q, "ask_price", 0),
            "bid_size": getattr(q, "bid_size", 0),
            "ask_size": getattr(q, "ask_size", 0),
        })
    except Exception as e:  # noqa: BLE001
        print(f"[tape] latest quote failed {symbol}: {e}")
        return {}


def _fetch_prints(symbol: str, key: str, secret: str, limit: int = _PRINT_LIMIT) -> list[dict]:
    """Recent trades (time & sales) via IEX historical feed. Never raises."""
    try:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockTradesRequest
        from alpaca.data.enums import DataFeed
        client = StockHistoricalDataClient(key, secret)
        start = datetime.now(timezone.utc) - timedelta(minutes=30)
        req = StockTradesRequest(symbol_or_symbols=symbol, start=start,
                                 limit=limit, feed=DataFeed.IEX)
        resp = client.get_stock_trades(req)
        trades = resp.data.get(symbol, []) if hasattr(resp, "data") else []
        out: list[dict] = []
        for t in trades[-limit:]:
            ts = getattr(t, "timestamp", None)
            out.append({
                "t": ts.isoformat() if ts is not None else None,
                "price": float(getattr(t, "price", 0) or 0),
                "size": float(getattr(t, "size", 0) or 0),
            })
        out.reverse()  # newest first
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[tape] prints fetch failed {symbol}: {e}")
        return []


@router.get("/{symbol}")
def tape(symbol: str) -> dict:
    """Recent prints + top-of-book depth/imbalance for ``symbol``.

    Keyless or on error: ``{"symbol": symbol, "prints": [], "book": {}}``.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return _empty("")

    book = _book_from_snapshot(symbol)
    prints: list[dict] = []

    key, secret = _keys()
    if key and secret:
        if not book:
            book = _fetch_book(symbol, key, secret)
        prints = _fetch_prints(symbol, key, secret)

    return {"symbol": symbol, "prints": prints, "book": book}
