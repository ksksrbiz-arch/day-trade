"""Security Master (DES/FA + House View) — single-security deep dive.

Aggregates everything the system knows about ONE ticker into a single payload:

* **Fundamentals (DES/FA)** — :mod:`trader.fundamentals` valuation/quality/growth
  scorecard (Alpha Vantage OVERVIEW, cached; pure-scored offline).
* **House view** — the confluence conviction from :mod:`trader.alpha`, the
  cross-layer mesh consensus from :mod:`trader.mesh_consensus`, a best-effort
  council take (cached only — never a live LLM call in the request path) and the
  RL voice pulled out of the confluence blend.
* **Signal scorecard** — recent :mod:`trader.sigtrack` calls for the symbol.

KEYLESS-SAFE: this environment has no Alpaca / Alpha Vantage keys. Every part is
wrapped so a missing key or absent data yields an empty/partial shape at HTTP 200
rather than a 500. Nothing here raises out of the handler.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/api/security", tags=["security"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fundamentals(symbol: str) -> dict:
    """DES/FA scorecard for the symbol. Empty (with a note) when unavailable."""
    try:
        from trader import fundamentals as _f
        fund = _f.get_fundamentals(symbol)
        if fund is not None:
            return {"available": True, **fund.as_log()}
    except Exception as ex:  # noqa: BLE001
        return {"available": False, "error": str(ex)[:120]}
    return {"available": False, "note": "no cached fundamentals (needs ALPHAVANTAGE_API_KEY)"}


def _recent_closes(symbol: str) -> list:
    """Best-effort daily closes. Returns [] without keys/data (never raises)."""
    try:
        import os
        from trader.marketdata import MarketData
        md = MarketData(os.environ.get("ALPACA_API_KEY", ""),
                        os.environ.get("ALPACA_SECRET_KEY", ""))
        closes = md.recent_closes(symbol)
        return list(closes) if closes else []
    except Exception:  # noqa: BLE001
        return []


def _confluence(symbol: str, fundamental_score, closes: list) -> dict:
    """Run the house confluence engine best-effort. Partial on any failure."""
    try:
        from trader import alpha
        conv = alpha.analyze(closes, symbol=symbol,
                             fundamental_score=fundamental_score, use_rl=True)
        return {
            "available": True,
            "composite": conv.composite,
            "side": conv.side,
            "agree": conv.agree,
            "n_methods": conv.n_methods,
            "gate_pass": conv.gate_pass,
            "size_mult": conv.size_mult,
            "scores": conv.scores,
            "weights": conv.weights,
            "reason": conv.reason,
        }
    except Exception as ex:  # noqa: BLE001
        return {"available": False, "error": str(ex)[:120]}


def _mesh(symbol: str) -> dict:
    """Cross-layer mesh consensus for the symbol, if it appears in the window."""
    try:
        from trader import mesh_consensus
        con = mesh_consensus.consensus()
        for s in con.get("symbols", []):
            if (s.get("symbol") or "").upper() == symbol.upper():
                return {"available": True, **s}
        return {"available": False, "note": "no recent mesh consensus for symbol"}
    except Exception as ex:  # noqa: BLE001
        return {"available": False, "error": str(ex)[:120]}


def _council(symbol: str) -> dict:
    """Cached council context only — NEVER a live LLM call in the request path."""
    try:
        from trader import market_brain
        regime = market_brain.cached_regime("neutral")
        return {"available": False, "regime": regime,
                "note": "live council disabled in request path"}
    except Exception as ex:  # noqa: BLE001
        return {"available": False, "error": str(ex)[:120]}


def _signals(symbol: str, limit: int = 10) -> dict:
    """Recent recorded directional signals for the symbol (cheap DB read)."""
    try:
        from trader import sigtrack
        c = sigtrack.conn()
        try:
            rows = c.execute(
                "SELECT ts, source, side, strength, status, fwd_ret, hit "
                "FROM signals WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol.upper(), int(limit))).fetchall()
        finally:
            c.close()
        recent = [dict(r) for r in rows]
        return {"available": True, "count": len(recent), "recent": recent}
    except Exception as ex:  # noqa: BLE001
        return {"available": False, "error": str(ex)[:120]}


def get_security(symbol: str) -> dict:
    """Aggregate fundamentals + house view + signal scorecard for one ticker.

    Always returns a dict at HTTP 200; every sub-part is keyless-safe.
    """
    symbol = (symbol or "").strip().upper() or "AAPL"

    fundamentals = _fundamentals(symbol)
    fund_score = fundamentals.get("fundamental_score") if fundamentals.get("available") else None

    closes = _recent_closes(symbol)
    confluence = _confluence(symbol, fund_score, closes)
    # Surface the RL voice pulled out of the blended confluence scores.
    rl = None
    if confluence.get("available"):
        rl = (confluence.get("scores") or {}).get("rl")

    house_view = {
        "confluence": confluence,
        "mesh": _mesh(symbol),
        "council": _council(symbol),
        "rl": rl,
    }

    return {
        "symbol": symbol,
        "fundamentals": fundamentals,
        "house_view": house_view,
        "signals": _signals(symbol),
        "bars": len(closes),
        "updated": _now_iso(),
    }


@router.get("/{symbol}")
def security_master(symbol: str) -> dict:
    """GET /api/security/{symbol} — the full single-security deep dive."""
    return get_security(symbol)
