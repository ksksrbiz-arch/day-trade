"""Cross-Asset (XA) — crypto, FX, rates, and commodities in one board.

Roadmap Phase 9. A single ``GET /api/xasset`` endpoint that assembles a compact
cross-asset snapshot from the free/cheap data we can actually reach:

  * **crypto**      — live prices + 24h change from CoinGecko (keyless), falling
    back to the resilient :mod:`trader.spot` multi-source price feed.
  * **fx**          — major pairs via Alpha Vantage ``CURRENCY_EXCHANGE_RATE``.
  * **rates**       — US 10y / 2y treasury yields via Alpha Vantage.
  * **commodities** — WTI / Brent crude via Alpha Vantage.

Alpha Vantage's free tier is rate-limited (~25 req/day), so every AV series is
cached for hours — the panel is a slow-moving macro board, not a ticker.

KEYLESS / OFFLINE-SAFE: with no ALPHAVANTAGE_API_KEY the fx/rates/commodities
blocks return empty; any network failure degrades to empty. The handler never
raises. Data is honestly labelled with its ``source``.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from fastapi import APIRouter

router = APIRouter(prefix="/api/xasset", tags=["xasset"])

_AV = "https://www.alphavantage.co/query"
_AV_TTL = 6 * 3600      # 6h: AV free tier is ~25 calls/day
_CRYPTO_TTL = 120       # 2min for crypto prices
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _get_json(url: str, timeout: float = 6.0) -> dict:
    """GET a JSON document, or {} on any failure (never raises)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-trader"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return {}


def _av(params: dict) -> dict:
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not key:
        return {}
    url = _AV + "?" + urllib.parse.urlencode({**params, "apikey": key})
    return _get_json(url, timeout=10.0)


def _num(v):
    try:
        if v in (None, "", "."):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# per-asset-class builders (each keyless/offline-safe)                         #
# --------------------------------------------------------------------------- #
_CRYPTO_IDS = [
    ("bitcoin", "Bitcoin", "BTC"), ("ethereum", "Ethereum", "ETH"),
    ("solana", "Solana", "SOL"), ("ripple", "XRP", "XRP"),
    ("dogecoin", "Dogecoin", "DOGE"),
]


def _crypto() -> list[dict]:
    def _fetch():
        ids = ",".join(c[0] for c in _CRYPTO_IDS)
        return _get_json(
            "https://api.coingecko.com/api/v3/simple/price?ids=" + ids +
            "&vs_currencies=usd&include_24hr_change=true")
    d = _cached("crypto", _CRYPTO_TTL, _fetch)
    out = []
    if isinstance(d, dict) and d:
        for cid, name, tkr in _CRYPTO_IDS:
            row = d.get(cid)
            if row and _num(row.get("usd")) is not None:
                out.append({"name": name, "symbol": tkr,
                            "value": round(_num(row["usd"]), 4),
                            "change": round(_num(row.get("usd_24h_change")) or 0.0, 2),
                            "unit": "%", "source": "coingecko"})
    if out:
        return out
    # fallback: resilient price-only feed (keyless)
    try:
        from trader import spot
        got, src = spot.spots([c[2] + "USD" for c in _CRYPTO_IDS])
        for cid, name, tkr in _CRYPTO_IDS:
            px = _num((got or {}).get(tkr + "USD"))
            if px is not None:
                out.append({"name": name, "symbol": tkr, "value": round(px, 4),
                            "change": None, "unit": "%", "source": src})
    except Exception:  # noqa: BLE001
        pass
    return out


def _fx() -> list[dict]:
    out = []
    for a, b in (("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY")):
        d = _cached(f"fx:{a}{b}", _AV_TTL,
                    lambda a=a, b=b: _av({"function": "CURRENCY_EXCHANGE_RATE",
                                          "from_currency": a, "to_currency": b}))
        rate = _num((d.get("Realtime Currency Exchange Rate", {}) or {}).get("5. Exchange Rate"))
        if rate is not None:
            out.append({"name": f"{a}/{b}", "value": round(rate, 4),
                        "change": None, "unit": "", "source": "alphavantage"})
    return out


def _series_latest(d: dict):
    """(latest, change-vs-prior) from an Alpha Vantage 'data' series, skipping '.'."""
    rows = [r for r in (d.get("data") or []) if _num(r.get("value")) is not None]
    if not rows:
        return None, None
    latest = _num(rows[0]["value"])
    prev = _num(rows[1]["value"]) if len(rows) > 1 else None
    return latest, (round(latest - prev, 3) if prev is not None else None)


def _rates() -> list[dict]:
    out = []
    for mat, label in (("10year", "US 10Y"), ("2year", "US 2Y")):
        d = _cached(f"ty:{mat}", _AV_TTL,
                    lambda mat=mat: _av({"function": "TREASURY_YIELD",
                                         "interval": "daily", "maturity": mat}))
        v, chg = _series_latest(d)
        if v is not None:
            out.append({"name": label, "value": round(v, 3), "change": chg,
                        "unit": "%", "source": "alphavantage"})
    return out


def _commodities() -> list[dict]:
    out = []
    for fn, label, unit in (("WTI", "WTI Crude", "$/bbl"), ("BRENT", "Brent Crude", "$/bbl")):
        d = _cached(f"co:{fn}", _AV_TTL,
                    lambda fn=fn: _av({"function": fn, "interval": "daily"}))
        v, chg = _series_latest(d)
        if v is not None:
            out.append({"name": label, "value": round(v, 2), "change": chg,
                        "unit": unit, "source": "alphavantage"})
    return out


def get_xasset() -> dict:
    """Assemble the cross-asset board. Never raises; blocks degrade to empty."""
    def _safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return []
    return {
        "crypto": _safe(_crypto),
        "fx": _safe(_fx),
        "rates": _safe(_rates),
        "commodities": _safe(_commodities),
        "note": "crypto: CoinGecko (2m) · fx/rates/commodities: Alpha Vantage (cached 6h, free-tier rate-limited)",
    }


@router.get("")
def xasset():
    """Cross-asset snapshot: crypto, FX, rates, commodities."""
    return get_xasset()
