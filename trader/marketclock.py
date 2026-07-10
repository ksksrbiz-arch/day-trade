"""Is the US equity market open right now?

One small, dependency-free helper the whole platform can ask. It prefers
Alpaca's authoritative ``/v2/clock`` (holiday- and half-day-aware) using the
same paper keys the trader already has, and falls back to a plain UTC weekday /
regular-hours heuristic when the API can't be reached.

    is_open()   -> bool
    is_closed() -> bool
    session()   -> "open" | "premarket" | "afterhours" | "closed_weekend" | "closed"
    snapshot()  -> full dict (cached ~5 min)

Honest scope: the offline fallback approximates regular hours as Mon-Fri
13:30-20:00 UTC and does NOT know market holidays; whenever the Alpaca key is
present (as it is on the cloud) the authoritative clock is used instead.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

_CACHE: dict = {"ts": 0.0, "data": None}
_TTL = 300.0  # 5 min -- the market state changes slowly


def _alpaca_clock() -> dict | None:
    key = os.getenv("ALPACA_API_KEY", "")
    sec = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not sec:
        return None
    # paper + live share the same clock; use paper host to match our creds
    url = "https://paper-api.alpaca.markets/v2/clock"
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": sec,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {
            "is_open": bool(d.get("is_open")),
            "next_open": d.get("next_open"),
            "next_close": d.get("next_close"),
            "source": "alpaca",
        }
    except Exception:  # noqa: BLE001
        return None


def _heuristic_clock() -> dict:
    """Offline fallback: Mon-Fri, 13:30-20:00 UTC ~= 9:30-16:00 ET (no holidays)."""
    now = datetime.now(timezone.utc)
    wd = now.weekday()  # 0=Mon .. 6=Sun
    minutes = now.hour * 60 + now.minute
    open_min, close_min = 13 * 60 + 30, 20 * 60
    weekend = wd >= 5
    is_open = (not weekend) and (open_min <= minutes < close_min)
    return {"is_open": is_open, "weekend": weekend, "minutes_utc": minutes,
            "next_open": None, "next_close": None, "source": "heuristic"}


def snapshot() -> dict:
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    d = _alpaca_clock() or _heuristic_clock()
    # derive a human session label
    dt = datetime.now(timezone.utc)
    wd = dt.weekday()
    minutes = dt.hour * 60 + dt.minute
    if d.get("is_open"):
        sess = "open"
    elif wd >= 5:
        sess = "closed_weekend"
    elif minutes < 13 * 60 + 30 and minutes >= 8 * 60:
        sess = "premarket"
    elif 20 * 60 <= minutes < 24 * 60:
        sess = "afterhours"
    else:
        sess = "closed"
    d["session"] = sess
    d["ts"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    _CACHE["ts"], _CACHE["data"] = now, d
    return d


def is_open() -> bool:
    return bool(snapshot().get("is_open"))


def is_closed() -> bool:
    return not is_open()


def session() -> str:
    return snapshot().get("session", "closed")


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2))
