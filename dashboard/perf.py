"""
Per-strategy performance: cumulative REALIZED P&L per bot.

Every order is tagged with its bot id via client_order_id ("<botid>-<rand>").
We pull filled orders, group by bot, and FIFO-match entries against exits per
symbol to produce a cumulative realized-P&L timeline -- an honest head-to-head
even though all bots share one paper account.

Caveat: this is REALIZED P&L only (closed round-trips). Open positions aren't
split per bot (the broker doesn't attribute unrealized P&L by tag). Options use
a 100x contract multiplier.
"""
from __future__ import annotations

from collections import defaultdict, deque


def _is_option(sym: str) -> bool:
    # OCC symbols look like AAPL260626C00297500 (>= 15 chars, has C/P + digits tail)
    return len(sym) >= 15 and sym[-9] in ("C", "P")


def _bot_of(client_order_id: str | None) -> str:
    if not client_order_id or "-" not in client_order_id:
        return "main"
    return client_order_id.split("-", 1)[0]


def fetch_fills(trading) -> list[dict]:
    """Return filled orders as simple dicts, oldest first."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    out = []
    try:
        orders = trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=500))
    except Exception:
        return out
    for o in orders:
        fq = float(getattr(o, "filled_qty", 0) or 0)
        fp = getattr(o, "filled_avg_price", None)
        if fq <= 0 or fp is None:
            continue
        ts = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
        out.append({
            "bot": _bot_of(getattr(o, "client_order_id", "")),
            "symbol": o.symbol,
            "side": str(getattr(o.side, "value", o.side)).lower(),
            "qty": fq,
            "price": float(fp),
            "ts": ts.isoformat() if ts else "",
        })
    out.sort(key=lambda r: r["ts"])
    return out


def realized_curves(trading, id_to_name: dict[str, str] | None = None) -> dict:
    """{bot_name: [{t, pnl}]} cumulative realized P&L per bot."""
    fills = fetch_fills(trading)
    # per (bot, symbol) FIFO lots
    lots: dict = defaultdict(deque)      # (bot,sym) -> deque[(qty, price, side)]
    cum: dict = defaultdict(float)
    curves: dict = defaultdict(list)
    id_to_name = id_to_name or {}
    for f in fills:
        bot, sym = f["bot"], f["symbol"]
        mult = 100.0 if _is_option(sym) else 1.0
        key = (bot, sym)
        q = f["qty"]
        # opposing side closes existing lots; same side opens
        dq = lots[key]
        realized = 0.0
        if dq and dq[0][2] != f["side"]:
            remaining = q
            while remaining > 1e-9 and dq and dq[0][2] != f["side"]:
                lq, lp, lside = dq[0]
                m = min(lq, remaining)
                # long lot closed by sell: (exit-entry); short lot closed by buy: (entry-exit)
                if lside == "buy":
                    realized += (f["price"] - lp) * m * mult
                else:
                    realized += (lp - f["price"]) * m * mult
                remaining -= m
                if m >= lq - 1e-9:
                    dq.popleft()
                else:
                    dq[0] = (lq - m, lp, lside)
            if remaining > 1e-9:
                dq.append((remaining, f["price"], f["side"]))
        else:
            dq.append((q, f["price"], f["side"]))
        if realized:
            name = id_to_name.get(bot, bot)
            cum[name] += realized
            curves[name].append({"t": f["ts"], "pnl": round(cum[name], 2)})
    return curves
