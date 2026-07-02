"""Alpha-Engine voice -- adapter to the prediction-alpha-engine service.

prediction-alpha-engine is the sovereign Kalshi/Polymarket opportunity scout
(separate repo, runs as its own FastAPI service). Its /cortex endpoints
translate scored prediction-market opportunities into per-ticker directional
signals -- "the event markets are pricing an 80% recession, that's bearish
SPY". This module makes that service a callable voice of the confluence
brain and the neural core, in the same shape as predict/tnet:

    score_signal(symbol) -> float in [-1, 1] | None   (None = abstain)

Fail-safe by construction: the service being down, slow, or unmapped for a
symbol just means the voice abstains (None) -- confluence blends without it
and no exception ever reaches the trade cycle. A short TTL cache keeps the
hot analyze() path off the network.

Config (env):
    ALPHA_ENGINE_URL   base URL of the service (default http://localhost:8000)
    USE_ALPHA_ENGINE   master switch (default on)
"""
from __future__ import annotations

import os
import time

TTL = 120.0          # seconds a fetched signal stays fresh
_TIMEOUT = 3.0       # keep the confluence path snappy when the service is down

_cache: dict[str, tuple[float, dict | None]] = {}


def base_url() -> str:
    return os.getenv("ALPHA_ENGINE_URL", "https://prediction-alpha-engine.onrender.com").rstrip("/")


def enabled() -> bool:
    v = os.getenv("USE_ALPHA_ENGINE")
    if v is None:
        return True
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _get(path: str, params: dict | None = None):
    import requests
    r = requests.get(base_url() + path, params=params or {}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def signal_for(symbol: str, ttl: float = TTL) -> dict | None:
    """Full signal payload {symbol, score, n, confidence, components} or None.

    Failures are negative-cached for the same TTL so a dead service costs one
    timeout per symbol per TTL window, not one per analyze() call."""
    if not enabled():
        return None
    symbol = symbol.upper()
    now = time.time()
    hit = _cache.get(symbol)
    if hit and now - hit[0] < ttl:
        return hit[1]
    sig = None
    try:
        d = _get("/cortex/signal", {"symbol": symbol})
        if isinstance(d, dict) and d.get("score") is not None:
            sig = d
    except Exception:  # noqa: BLE001
        sig = None
    _cache[symbol] = (now, sig)
    return sig


def score_signal(symbol: str) -> float | None:
    """Scalar in [-1,1] for confluence/cortex (None if unavailable/unmapped)."""
    sig = signal_for(symbol)
    if not sig:
        return None
    try:
        return max(-1.0, min(1.0, float(sig["score"])))
    except (KeyError, TypeError, ValueError):
        return None


def signals(symbols: list[str]) -> list[dict]:
    """Batch signals for the dashboard watchlist sweep (one HTTP call)."""
    if not enabled() or not symbols:
        return []
    try:
        d = _get("/cortex/signals", {"symbols": ",".join(s.upper() for s in symbols)})
        return d if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


def status() -> dict:
    """Service card for the dashboard: reachable? how many markets cached?"""
    out = {"enabled": enabled(), "url": base_url(), "reachable": False}
    if not enabled():
        return out
    try:
        h = _get("/health")
        out["reachable"] = True
        out["cached_opportunities"] = h.get("cached_opportunities")
    except Exception:  # noqa: BLE001
        pass
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2))
    print("SPY:", score_signal("SPY"))
