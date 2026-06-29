"""
Historical daily-bar loader for backtesting. Returns an ALIGNED panel:

    dates:  [YYYY-MM-DD, ...]                  (common trading days, ascending)
    prices: {symbol: [close, ...]}             (same length as dates)

Sources (pick via `source`):
  * "stooq"  -- FREE, no key, decades of daily history (CSV over HTTP)
  * "tiingo" -- free key, clean split/dividend-adjusted EOD
  * "massive"-- whole-market flat files (paid entitlement)
  * "alpaca" -- daily bars from the trading account (≈2y free)
  * "auto"   -- massive if entitled, else alpaca

All sources are interchangeable -- the rest of the backtest never changes.
"""
from __future__ import annotations

import csv
import io
import time
import urllib.request
from datetime import datetime, timedelta, timezone

_UA = "paper-trader/1.0"


def _stooq_panel(symbols, days):
    """FREE daily history from Stooq. US tickers map to '<sym>.us'."""
    per = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days * 1.6) + 10)).date().isoformat()
    for s in symbols:
        url = f"https://stooq.com/q/d/l/?s={s.lower()}.us&i=d"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"[stooq] {s} fetch failed: {e}")
            per[s] = {}; continue
        m = {}
        rdr = csv.DictReader(io.StringIO(text))
        for row in rdr:
            d = row.get("Date", "")
            c = row.get("Close", "")
            if not d or d < cutoff:
                continue
            try:
                m[d] = float(c)
            except (TypeError, ValueError):
                continue
        per[s] = m
        time.sleep(0.15)  # be polite
    return per


def _tiingo_panel(symbols, days, token):
    """Clean adjusted EOD from Tiingo (free key)."""
    import json
    per = {}
    start = (datetime.now(timezone.utc) - timedelta(days=int(days * 1.6) + 10)).date().isoformat()
    for s in symbols:
        url = (f"https://api.tiingo.com/tiingo/daily/{s}/prices"
               f"?startDate={start}&token={token}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                       "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                rows = json.loads(r.read().decode())
        except Exception as e:
            print(f"[tiingo] {s} fetch failed: {e}")
            per[s] = {}; continue
        m = {}
        for row in rows:
            d = (row.get("date", "") or "")[:10]
            c = row.get("adjClose", row.get("close"))
            if d and c is not None:
                m[d] = float(c)
        per[s] = m
        time.sleep(0.1)
    return per


def _binance_symbol(s):
    s = s.upper().replace("/", "")
    if s.endswith("USD") and not s.endswith("USDT"):
        s = s[:-3] + "USDT"
    return s


def _binance_panel(symbols, days):
    """FREE crypto daily history from Binance public klines (no key)."""
    import json
    per = {}
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days + 5)).timestamp() * 1000)
    for s in symbols:
        bsym = _binance_symbol(s)
        m = {}
        cur = start_ms
        try:
            while True:
                url = (f"https://api.binance.us/api/v3/klines?symbol={bsym}"
                       f"&interval=1d&limit=1000&startTime={cur}")
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=25) as r:
                    rows = json.loads(r.read().decode())
                if not rows:
                    break
                for k in rows:
                    d = datetime.fromtimestamp(k[0] / 1000, timezone.utc).strftime("%Y-%m-%d")
                    m[d] = float(k[4])
                cur = rows[-1][0] + 86400000
                if len(rows) < 1000:
                    break
                time.sleep(0.1)
        except Exception as e:
            print(f"[binance] {s} fetch failed: {e}")
        per[s] = m
    return per


def _coinex_panel(symbols, days):
    """FREE crypto daily klines from CoinEx public API (no key).
    CoinEx kline row = [time, open, close, high, low, volume, value]."""
    import json
    per = {}
    limit = min(1000, max(60, int(days) + 5))
    for s in symbols:
        bsym = s.upper().replace("/", "")
        if bsym.endswith("USD") and not bsym.endswith("USDT"):
            bsym = bsym[:-3] + "USDT"
        m = {}
        try:
            url = (f"https://api.coinex.com/v1/market/kline?market={bsym}"
                   f"&type=1day&limit={limit}")
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.loads(r.read().decode())
            for k in (d.get("data") or []):
                ts = datetime.fromtimestamp(int(k[0]), timezone.utc).strftime("%Y-%m-%d")
                m[ts] = float(k[2])   # close
        except Exception as e:  # noqa: BLE001
            print(f"[coinex] {s} failed: {e}")
        per[s] = m
        time.sleep(0.1)
    return per


def _alpaca_panel(symbols, days, key, secret):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    d = StockHistoricalDataClient(key, secret)
    start = datetime.now(timezone.utc) - timedelta(days=days + 5)
    req = StockBarsRequest(symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day,
                           start=start, feed=DataFeed.IEX)
    data = d.get_stock_bars(req).data
    return {s: {b.timestamp.strftime("%Y-%m-%d"): float(b.close) for b in data.get(s, [])}
            for s in symbols}


def _massive_panel(symbols, days, massive):
    per = {s: {} for s in symbols}
    d = datetime.now(timezone.utc).date()
    scanned = 0
    sset = set(symbols)
    while scanned < days + 5:
        ds = d.isoformat(); d = d - timedelta(days=1); scanned += 1
        csv_text = massive.day_aggs_csv(ds)
        if not csv_text:
            continue
        rows = massive.parse_day_aggs(csv_text)
        for s in sset:
            r = rows.get(s)
            if r:
                per[s][ds] = r["close"]
    return per


def _crsp_panel(symbols, days):
    """Offline panel from the CRSP-lite price cache (includes delisted names).
    Uses crsp.query.get_prices which serves cache and fetches once if missing."""
    from datetime import datetime, timedelta, timezone
    from .crsp import query as crsp
    start = (datetime.now(timezone.utc) - timedelta(days=int(days * 1.6) + 10)).date().isoformat()
    per = {}
    for s in symbols:
        try:
            bars = crsp.get_prices(s, start, None)
            per[s] = {b["date"]: float(b["adj_close"]) for b in bars
                      if b.get("date") and b.get("adj_close") is not None}
        except Exception as e:  # noqa: BLE001
            print(f"[crsp] {s} failed: {e}")
            per[s] = {}
    return per


def load_panel(symbols, days=750, key="", secret="", massive=None,
               source="auto", tiingo_token=""):
    if source == "crsp":
        per, src = _crsp_panel(symbols, days), "crsp"
    elif source == "stooq":
        per, src = _stooq_panel(symbols, days), "stooq"
    elif source == "tiingo":
        per, src = _tiingo_panel(symbols, days, tiingo_token), "tiingo"
    elif source == "binance":
        per, src = _binance_panel(symbols, days), "binance"
    elif source == "coinex":
        per, src = _coinex_panel(symbols, days), "coinex"
    elif source == "massive" or (source == "auto" and massive is not None and massive.can_download()):
        per, src = _massive_panel(symbols, days, massive), "massive"
    else:
        per, src = _alpaca_panel(symbols, days, key, secret), "alpaca"
    # align on dates shared by symbols that actually returned data, so a single
    # failed/empty fetch can't collapse the whole panel.
    have = {s: m for s, m in per.items() if m}
    common = None
    for m in have.values():
        ds = set(m.keys())
        common = ds if common is None else (common & ds)
    common = sorted(common or [])
    prices = {s: [m[d] for d in common] for s, m in have.items()}
    return {"dates": common, "prices": prices, "source": src,
            "missing": [s for s in symbols if s not in have]}
