"""Portfolio Analytics (PORT) — exposures, attribution, and risk metrics.

One structured endpoint, ``GET /api/port``, that fuses three views of the book:

  * **exposures**  — per-position market value + portfolio weight (from the live
    paper account), so you can see concentration at a glance.
  * **attribution** — which *voice* actually makes money, decomposed from the
    backprop decision log via :func:`trader.attribution.report`.
  * **risk**       — gross/net/long/short exposure, position count, largest
    single-name weight, cash %, day P&L %, and peak-to-trough drawdown of the
    account equity curve (via :func:`trader.metrics.max_drawdown`).

KEYLESS-SAFE: with no Alpaca account (or any live-call failure) every block
degrades to an empty list / zeroed KPIs — the shape is always the same and the
handler never raises. This module NEVER trades; it only reads.
"""
from __future__ import annotations

from fastapi import APIRouter

from trader import attribution, config, metrics

router = APIRouter(prefix="/api/port", tags=["port"])


def _empty_risk() -> dict:
    return {
        "equity": 0.0,
        "cash": 0.0,
        "cash_pct": 0.0,
        "positions": 0,
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "long_exposure": 0.0,
        "short_exposure": 0.0,
        "gross_pct": 0.0,
        "net_pct": 0.0,
        "max_weight": 0.0,
        "day_pl": 0.0,
        "day_pl_pct": 0.0,
        "max_drawdown_pct": 0.0,
    }


def _trading():
    """Fresh paper TradingClient from config. Raises if the SDK/keys are absent —
    callers wrap this so a keyless environment degrades to an empty shape."""
    from alpaca.trading.client import TradingClient

    cfg = config.load()
    return TradingClient(cfg.alpaca_key, cfg.alpaca_secret, paper=True)


def _exposures_and_risk(tc) -> tuple[list[dict], dict, str | None]:
    """Live positions -> (exposures rows, risk KPIs, error-or-None).

    ``tc`` is a TradingClient (or None when construction failed). Every branch is
    guarded: on any failure we return the zeroed shape plus a short error string
    rather than propagating the exception.
    """
    risk = _empty_risk()
    if tc is None:
        return [], risk, "no trading client"
    try:
        acct = tc.get_account()
        positions = tc.get_all_positions()
    except Exception as e:  # noqa: BLE001 — keyless / offline / SDK missing
        return [], risk, str(e)[:120]

    try:
        equity = float(getattr(acct, "equity", 0.0) or 0.0)
        last_equity = float(getattr(acct, "last_equity", 0.0) or 0.0)
        cash = float(getattr(acct, "cash", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        equity = last_equity = cash = 0.0

    rows: list[dict] = []
    long_exp = 0.0
    short_exp = 0.0
    for p in positions:
        try:
            mv = float(p.market_value)
            qty = float(p.qty)
            side = str(getattr(p.side, "value", p.side) or "").lower() or ("short" if qty < 0 else "long")
            plpc = getattr(p, "unrealized_plpc", None)
            rows.append({
                "symbol": p.symbol,
                "qty": qty,
                "side": side,
                "market_value": round(mv, 2),
                "weight": 0.0,  # filled once we know the gross total
                "unrealized_plpc": round(float(plpc) * 100, 2) if plpc is not None else 0.0,
            })
            # Classify by the declared side (fall back to mv sign), so a broker
            # that reports a positive market_value for a short is still netted out.
            if side == "short":
                short_exp += abs(mv)
            else:
                long_exp += abs(mv)
        except Exception:  # noqa: BLE001 — skip a malformed position, keep the rest
            continue

    gross = long_exp + short_exp
    net = long_exp - short_exp
    # Weight each name by |market value| / gross exposure (concentration view).
    for r in rows:
        r["weight"] = round(abs(r["market_value"]) / gross * 100, 2) if gross else 0.0
    rows.sort(key=lambda r: abs(r["market_value"]), reverse=True)

    risk.update({
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "cash_pct": round(cash / equity * 100, 2) if equity else 0.0,
        "positions": len(rows),
        "gross_exposure": round(gross, 2),
        "net_exposure": round(net, 2),
        "long_exposure": round(long_exp, 2),
        "short_exposure": round(short_exp, 2),
        "gross_pct": round(gross / equity * 100, 2) if equity else 0.0,
        "net_pct": round(net / equity * 100, 2) if equity else 0.0,
        "max_weight": max((r["weight"] for r in rows), default=0.0),
        "day_pl": round(equity - last_equity, 2) if last_equity else 0.0,
        "day_pl_pct": round((equity / last_equity - 1) * 100, 3) if last_equity else 0.0,
    })
    return rows, risk, None


def _drawdown_pct(tc) -> float:
    """Peak-to-trough drawdown of the account equity curve, as a positive %.

    Reuses :func:`trader.metrics.max_drawdown` (fraction) over the portfolio
    history. Keyless-safe: returns 0.0 on any failure.
    """
    if tc is None:
        return 0.0
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        ph = tc.get_portfolio_history(
            GetPortfolioHistoryRequest(period="30D", timeframe="1D"))
        eq = [float(x) for x in (ph.equity or []) if x is not None]
        return round(metrics.max_drawdown(eq) * 100, 3)
    except Exception:  # noqa: BLE001
        return 0.0


def _attribution() -> list[dict]:
    """Voice attribution rows from the backprop decision log. Never raises."""
    try:
        rep = attribution.report()
        out = []
        for v in rep.get("voices", []):
            out.append({
                "voice": v.get("voice"),
                "weight": v.get("weight", 0.0),
                "attributed_return_pct": v.get("attributed_return_pct", 0.0),
                "opinions": v.get("opinions", 0),
                "verdict": v.get("verdict", ""),
            })
        return out
    except Exception:  # noqa: BLE001
        return []


@router.get("")
def port() -> dict:
    """Portfolio attribution, exposures, and risk in one structured payload."""
    try:
        tc = _trading()
    except Exception:  # noqa: BLE001 — keyless / SDK missing
        tc = None
    exposures, risk, err = _exposures_and_risk(tc)
    risk["max_drawdown_pct"] = _drawdown_pct(tc)
    out = {
        "exposures": exposures,
        "attribution": _attribution(),
        "risk": risk,
    }
    if err:
        # Structured, non-fatal: the UI still renders the zeroed shape + note.
        out["note"] = f"account unavailable: {err}"
    return out
