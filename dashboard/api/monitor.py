"""Market Monitor (MOST) — movers, breadth, most-active, and sector heat.

One structured endpoint, ``GET /api/monitor``, that scans the trading universe
once (via :func:`trader.scanner.scan`, which already computes 5-day move, volume
spike and a momentum thesis per name) and reshapes it into the classic terminal
"what's moving" views:

  * **movers**       — top gainers / losers by 5-day price move.
  * **most_active**  — names with the biggest relative-volume spike.
  * **breadth**      — advancers vs decliners across the scanned universe.
  * **sectors**      — average move per sector (a static sector map keeps the
    grouping real without a data dependency).

KEYLESS-SAFE: without Alpaca keys ``scanner.scan`` returns an empty list, so
every block degrades to empty / zeroed and the handler never raises.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter

from trader import scanner

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# Compact sector map for the momentum universe; unknowns fall back to "Other".
_SECTOR = {
    "SPY": "Index", "QQQ": "Index", "IWM": "Index", "DIA": "Index",
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "AVGO": "Technology", "CRM": "Technology",
    "ORCL": "Technology", "ADBE": "Technology", "INTC": "Technology",
    "CSCO": "Technology", "QCOM": "Technology", "TXN": "Technology",
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication",
    "AMZN": "Consumer Disc.", "TSLA": "Consumer Disc.", "NKE": "Consumer Disc.",
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "PEP": "Consumer Staples", "KO": "Consumer Staples",
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials", "V": "Financials",
    "XOM": "Energy", "CVX": "Energy",
    "UNH": "Healthcare", "JNJ": "Healthcare", "LLY": "Healthcare", "PFE": "Healthcare",
}


def _sector(sym: str) -> str:
    return _SECTOR.get(sym.upper(), "Other")


def _row(r: dict) -> dict:
    """Trim a scanner row to the fields the monitor renders."""
    return {
        "symbol": r.get("symbol"),
        "price": r.get("price"),
        "move": r.get("price_move"),      # 5-day fractional move
        "vol_spike": r.get("vol_spike"),
        "thesis": r.get("thesis"),
        "sector": _sector(str(r.get("symbol", ""))),
    }


def _empty() -> dict:
    return {
        "movers": {"gainers": [], "losers": []},
        "most_active": [],
        "breadth": {"advancers": 0, "decliners": 0, "flat": 0, "adv_pct": 0.0, "total": 0},
        "sectors": [],
    }


def get_monitor(limit: int = 8) -> dict:
    """Assemble the market-monitor payload. Never raises."""
    try:
        rows = [_row(r) for r in (scanner.scan(min_conf=0.0) or [])]
    except Exception:  # noqa: BLE001
        return _empty()
    if not rows:
        return _empty()

    with_move = [r for r in rows if isinstance(r.get("move"), (int, float))]
    by_move = sorted(with_move, key=lambda r: r["move"], reverse=True)
    gainers = [r for r in by_move if r["move"] > 0][:limit]
    losers = [r for r in reversed(by_move) if r["move"] < 0][:limit]

    most_active = sorted(
        [r for r in rows if isinstance(r.get("vol_spike"), (int, float))],
        key=lambda r: r["vol_spike"], reverse=True)[:limit]

    adv = sum(1 for r in with_move if r["move"] > 0)
    dec = sum(1 for r in with_move if r["move"] < 0)
    flat = len(with_move) - adv - dec
    total = len(with_move)

    sec_moves: dict[str, list[float]] = defaultdict(list)
    for r in with_move:
        sec_moves[r["sector"]].append(r["move"])
    sectors = sorted(
        ({"sector": s, "avg_move": round(sum(v) / len(v), 4), "count": len(v)}
         for s, v in sec_moves.items()),
        key=lambda x: x["avg_move"], reverse=True)

    return {
        "movers": {"gainers": gainers, "losers": losers},
        "most_active": most_active,
        "breadth": {
            "advancers": adv, "decliners": dec, "flat": flat, "total": total,
            "adv_pct": round(adv / total * 100, 1) if total else 0.0,
        },
        "sectors": sectors,
    }


@router.get("")
def monitor(limit: int = 8):
    """Movers / breadth / most-active / sector heat for the MOST panel."""
    try:
        limit = max(1, min(25, int(limit)))
    except (TypeError, ValueError):
        limit = 8
    return get_monitor(limit)
