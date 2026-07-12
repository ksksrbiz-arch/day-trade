"""Options monitor (OMON) — grouped option-chain endpoint.

Serves an option chain for a single underlying, grouped by expiration, with
strike / call-put / greeks / implied-vol where the data feed provides it.

KEYLESS-SAFE: without Alpaca credentials (or on any live-call failure) the
endpoint returns a structured, empty payload — ``{"symbol": SYM, "chain": []}``
— and never raises. Live/Alpaca work is wrapped; a genuine upstream error is
surfaced as a 502 JSONResponse, never a 500/stack trace.

This module only READS the chain. It reuses the contract-selection primitives
from :mod:`trader.options` (``pick_contract``, ``OptionsBroker``) but never
places an order.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from trader import options as _options  # noqa: F401  (reuse: pick_contract/OptionsBroker)

router = APIRouter(prefix="/api/options", tags=["options"])

# OCC symbol, e.g. ``AAPL240119C00195000`` -> AAPL / 2024-01-19 / C / 195.0
_OCC_RE = re.compile(r"^(?P<root>[A-Z]+)(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")

_EMPTY = {"symbol": "", "chain": []}


def _keys() -> tuple[str, str]:
    """Best-effort Alpaca key/secret; empty strings when unconfigured."""
    try:
        from trader.config import load
        cfg = load()
        return getattr(cfg, "alpaca_key", "") or "", getattr(cfg, "alpaca_secret", "") or ""
    except Exception:
        return "", ""


def _parse_occ(sym: str) -> dict[str, Any]:
    """Parse an OCC option symbol into its parts (best-effort)."""
    m = _OCC_RE.match(sym or "")
    if not m:
        return {"expiry": "", "type": "", "strike": None}
    exp = m.group("exp")  # YYMMDD
    return {
        "expiry": f"20{exp[0:2]}-{exp[2:4]}-{exp[4:6]}",
        "type": "call" if m.group("cp") == "C" else "put",
        "strike": int(m.group("strike")) / 1000.0,
    }


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except Exception:
        return None


def _snapshot_row(sym: str, snap: Any) -> dict[str, Any]:
    """Flatten one OptionsSnapshot into a JSON-safe row."""
    parts = _parse_occ(sym)
    greeks = getattr(snap, "greeks", None)
    quote = getattr(snap, "latest_quote", None)
    trade = getattr(snap, "latest_trade", None)
    return {
        "symbol": sym,
        "expiry": parts["expiry"],
        "type": parts["type"],
        "strike": parts["strike"],
        "iv": _num(getattr(snap, "implied_volatility", None)),
        "bid": _num(getattr(quote, "bid_price", None)) if quote is not None else None,
        "ask": _num(getattr(quote, "ask_price", None)) if quote is not None else None,
        "last": _num(getattr(trade, "price", None)) if trade is not None else None,
        "delta": _num(getattr(greeks, "delta", None)) if greeks is not None else None,
        "gamma": _num(getattr(greeks, "gamma", None)) if greeks is not None else None,
        "theta": _num(getattr(greeks, "theta", None)) if greeks is not None else None,
        "vega": _num(getattr(greeks, "vega", None)) if greeks is not None else None,
        "rho": _num(getattr(greeks, "rho", None)) if greeks is not None else None,
    }


def _group_by_expiry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group flat rows into ``[{expiry, strikes:[{strike, call, put}]}]``."""
    by_exp: dict[str, dict[float, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        exp = r.get("expiry") or "—"
        strike = r.get("strike")
        if strike is None:
            continue
        cell = by_exp[exp].setdefault(strike, {"strike": strike, "call": None, "put": None})
        if r.get("type") in ("call", "put"):
            cell[r["type"]] = r
    out: list[dict[str, Any]] = []
    for exp in sorted(by_exp):
        strikes = [by_exp[exp][k] for k in sorted(by_exp[exp])]
        out.append({"expiry": exp, "strikes": strikes})
    return out


def _fetch_chain(symbol: str) -> Optional[list[dict[str, Any]]]:
    """Fetch + flatten the live option-chain snapshot. None => unavailable."""
    key, secret = _keys()
    if not (key and secret):
        return None
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest
        client = OptionHistoricalDataClient(key, secret)
        chain = client.get_option_chain(OptionChainRequest(underlying_symbol=symbol))
        rows = [_snapshot_row(sym, snap) for sym, snap in (chain or {}).items()]
        return rows
    except Exception as e:  # noqa: BLE001
        print(f"[options_api] chain fetch failed {symbol}: {e}")
        raise


@router.get("/{symbol}")
def option_chain(symbol: str) -> dict:
    """Grouped option chain for ``symbol``.

    Returns ``{"symbol", "chain":[{expiry, strikes:[{strike, call, put}]}]}``.
    Without keys: ``{"symbol": SYM, "chain": []}``. On upstream failure: a 502
    JSONResponse. Never raises / never 500s.
    """
    sym = (symbol or "").strip().upper()
    try:
        rows = _fetch_chain(sym)
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"symbol": sym, "chain": [], "error": "chain unavailable"},
        )
    if rows is None:
        # keyless / unconfigured — structured empty payload
        return {"symbol": sym, "chain": []}
    return {"symbol": sym, "chain": _group_by_expiry(rows)}
