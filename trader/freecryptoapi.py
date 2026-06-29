"""
FreeCryptoAPI client (https://api.freecryptoapi.com/v1/getData).

Adds crypto breadth + per-coin RSI/signal/market-cap to the Market Brain.
Bearer-key auth. Fail-soft: returns {} on any error so the brain still works
on the other free sources. Set FREECRYPTOAPI_KEY to activate.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

BASE = "https://api.freecryptoapi.com/v1"
_UA = "paper-trader/1.0"


class FreeCryptoClient:
    def __init__(self, api_key: str):
        self.key = api_key
        self.enabled = bool(api_key)

    def get_data(self, symbols) -> dict:
        """symbols: list or 'BTC+ETH'. Returns {symbol: {price,change_24h,rsi,signal,...}}."""
        if not self.enabled:
            return {}
        if isinstance(symbols, (list, tuple)):
            symbols = "+".join(symbols)
        url = f"{BASE}/getData?symbol={symbols}"
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.key}", "User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
        except Exception as e:
            print(f"[freecryptoapi] error (fail-soft): {e}")
            return {}
        out = {}
        rows = d.get("symbols") or d.get("data") or (d if isinstance(d, list) else [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            sym = str(row.get("symbol") or row.get("ticker") or "").upper()
            if not sym:
                continue
            out[sym] = {
                "price": _f(row.get("last") or row.get("price")),
                "change_24h": _f(row.get("change_24h") or row.get("daily_change_percentage")),
                "rsi": _f(row.get("rsi")),
                "signal": row.get("signal") or row.get("trend") or "",
                "market_cap": _f(row.get("market_cap")),
                "volume": _f(row.get("volume") or row.get("daily_volume")),
            }
        return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
