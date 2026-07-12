"""Equity screener (EQS) endpoint for the intelligence terminal.

Ranks the cross-sectional universe using the platform's real factor voice
(:mod:`trader.factors`) and enriches each row with the momentum scanner's
relative-volume / price context (:mod:`trader.scanner`). The result is a single
ranked table the ``screen`` terminal panel renders with sortable columns.

KEYLESS-SAFE: with no Alpaca keys ``trader.ml.dataset._alpaca_series`` returns
empty series, so ``factors.ranking()`` / ``scanner.scan()`` yield nothing. In
that case (and on any error) this endpoint returns ``{"results": [], "columns":
[]}`` rather than raising -- it never 500s.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from trader import factors, scanner, xsection  # noqa: F401  (xsection: shared factor lib)

router = APIRouter(prefix="/api/screen", tags=["screen"])

# Columns exposed to the panel (in display order). ``symbol`` is always first.
COLUMNS = [
    "symbol", "score", "mom", "reversal", "lowvol", "trend",
    "rvol", "price", "thesis", "confidence", "sector",
]

# Static sector map for the factor/scanner universe so the ``sector`` filter is
# real without a data dependency. Symbols not listed fall back to "Other".
_SECTOR = {
    "SPY": "Index", "QQQ": "Index", "IWM": "Index", "HYG": "Credit",
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "AVGO": "Technology", "CRM": "Technology",
    "ORCL": "Technology", "ADBE": "Technology", "INTC": "Technology",
    "CSCO": "Technology", "QCOM": "Technology", "TXN": "Technology",
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication",
    "AMZN": "Consumer Disc.", "TSLA": "Consumer Disc.",
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "PEP": "Consumer Staples", "KO": "Consumer Staples",
    "JPM": "Financials", "BAC": "Financials",
    "XOM": "Energy", "UNH": "Healthcare",
}


def _sector(sym: str) -> str:
    return _SECTOR.get(sym.upper(), "Other")


def _rows() -> list[dict]:
    """Merge the factor ranking with scanner context into flat row dicts.

    Each data call is wrapped so a failing source degrades to empty rather than
    propagating. Rows are keyed by symbol; the factor ranking is the spine and
    scanner fields (rvol, price, thesis, confidence) are joined when available.
    """
    try:
        rank = factors.ranking() or []
    except Exception:  # noqa: BLE001
        rank = []

    # scanner context keyed by symbol (min_conf=0 so nothing is filtered out).
    ctx: dict[str, dict] = {}
    try:
        for c in (scanner.scan(min_conf=0.0) or []):
            ctx[str(c.get("symbol", "")).upper()] = c
    except Exception:  # noqa: BLE001
        ctx = {}

    rows: list[dict] = []
    for r in rank:
        sym = str(r.get("symbol", "")).upper()
        if not sym:
            continue
        fac = r.get("factors", {}) or {}
        c = ctx.get(sym, {})
        rows.append({
            "symbol": sym,
            "score": r.get("score"),
            "mom": fac.get("mom"),
            "reversal": fac.get("reversal"),
            "lowvol": fac.get("lowvol"),
            "trend": fac.get("trend"),
            "rvol": c.get("vol_spike"),
            "price": c.get("price"),
            "thesis": c.get("thesis"),
            "confidence": c.get("confidence"),
            "sector": _sector(sym),
        })
    return rows


@router.get("")
def screen(
    min_score: Annotated[float, Query(description="minimum composite factor score")] = -1.0,
    min_rvol: Annotated[float, Query(description="minimum relative volume (vol spike)")] = 0.0,
    sector: Annotated[str, Query(description="sector filter (case-insensitive substring)")] = "",
    limit: Annotated[int, Query(ge=1, le=200, description="max rows returned")] = 50,
) -> dict:
    """Ranked screener table. Filters are applied server-side, then truncated.

    Returns ``{"results": [...], "columns": [...]}``. Keyless / on error the
    lists are empty. A genuine downstream fault returns a 502 JSONResponse
    (still structured JSON, never a 500 stack trace).
    """
    try:
        rows = _rows()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            status_code=502,
            content={"results": [], "columns": [], "error": str(e)[:120]},
        )

    sec = (sector or "").strip().lower()
    out = []
    for r in rows:
        sc = r.get("score")
        if sc is not None and sc < min_score:
            continue
        rv = r.get("rvol")
        if min_rvol > 0.0 and (rv is None or rv < min_rvol):
            continue
        if sec and sec not in str(r.get("sector", "")).lower():
            continue
        out.append(r)

    # already ranked by score desc (factors.ranking); preserve that order.
    out = out[: max(1, int(limit))]
    return {"results": out, "columns": COLUMNS}
