"""
Resilient real-time crypto spot prices from multiple free sources.

Adapted from Krypt Trader's crypto15m spot fetcher
(https://github.com/scripflipped/Krypt-Trader, MIT License). The portable idea:
never depend on ONE free price API -- they rate-limit and flake. Try a chain
(CryptoCompare -> Coinbase -> CoinGecko), cache briefly, and fall back to the
last good value (marked stale) rather than returning nothing.

This platform's crypto *history* comes from CoinEx; this module adds a hardened
*spot* read for the market brain and crypto voices. Sync + stdlib (urllib) to
match the rest of the price layer. Every call is fail-soft.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

# asset -> CoinGecko id (for the CoinGecko source)
_CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "BNB": "binancecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "LTC": "litecoin", "LINK": "chainlink", "MATIC": "matic-network", "DOT": "polkadot",
}
_UA = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; PlatformBrain/1.0)"}
_TTL = 12.0
_cache: dict = {"at": 0.0, "spots": {}, "source": "none"}


def _get(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def _norm(sym: str) -> str:
    """BTCUSD / BTC/USD / btc -> BTC."""
    s = sym.upper().replace("/", "").replace("-", "")
    for suf in ("USDT", "USD", "USDC"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s


def _src_cryptocompare(assets: list[str]) -> dict:
    q = urllib.parse.urlencode({"fsyms": ",".join(assets), "tsyms": "USD"})
    d = _get(f"https://min-api.cryptocompare.com/data/pricemulti?{q}")
    if isinstance(d, dict) and d.get("Response") == "Error":
        raise RuntimeError(d.get("Message", "cryptocompare error"))
    out = {}
    for a in assets:
        px = (d.get(a) or {}).get("USD")
        if px is not None:
            out[a] = float(px)
    return out


def _src_coinbase(assets: list[str]) -> dict:
    out = {}
    for a in assets:
        try:
            d = _get(f"https://api.coinbase.com/v2/prices/{a}-USD/spot")
            amt = ((d or {}).get("data") or {}).get("amount")
            if amt is not None:
                out[a] = float(amt)
        except Exception:  # noqa: BLE001
            continue
    return out


def _src_coingecko(assets: list[str]) -> dict:
    ids = [(_CG_IDS.get(a), a) for a in assets if _CG_IDS.get(a)]
    if not ids:
        return {}
    q = urllib.parse.urlencode({"ids": ",".join(i for i, _ in ids), "vs_currencies": "usd"})
    d = _get(f"https://api.coingecko.com/api/v3/simple/price?{q}")
    out = {}
    for cg, a in ids:
        px = (d.get(cg) or {}).get("usd")
        if px is not None:
            out[a] = float(px)
    return out


_SOURCES = [("cryptocompare", _src_cryptocompare), ("coinbase", _src_coinbase),
            ("coingecko", _src_coingecko)]


def spots(symbols) -> tuple[dict, str]:
    """Return ({ASSET: usd_price}, source) for the requested symbols. Cached
    (TTL) with a stale-value fallback so a flaky source never returns nothing."""
    assets = sorted({_norm(s) for s in symbols})
    if not assets:
        return {}, "none"
    now = time.time()
    if _cache["spots"] and (now - _cache["at"]) < _TTL and all(a in _cache["spots"] for a in assets):
        return {a: _cache["spots"][a] for a in assets}, _cache["source"]
    for name, fn in _SOURCES:
        try:
            got = fn(assets)
        except Exception:  # noqa: BLE001
            continue
        if got:
            merged = dict(_cache["spots"]); merged.update(got)
            _cache.update(at=now, spots=merged, source=name)
            return {a: got[a] for a in assets if a in got}, name
    if _cache["spots"] and all(a in _cache["spots"] for a in assets):
        return {a: _cache["spots"][a] for a in assets}, f"{_cache['source']} (stale)"
    return {}, "unavailable"


def spot(symbol: str) -> float | None:
    """Single spot price in USD, or None. Fail-soft."""
    got, _ = spots([symbol])
    return got.get(_norm(symbol))


def status() -> dict:
    return {"cached": list(_cache["spots"].keys()), "source": _cache["source"],
            "age_s": round(time.time() - _cache["at"], 1) if _cache["at"] else None}


if __name__ == "__main__":
    print(spots(["BTCUSD", "ETH/USD", "SOL", "DOGEUSD"]))
