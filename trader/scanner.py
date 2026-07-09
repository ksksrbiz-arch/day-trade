"""
Momentum / catalyst scanner over daily bars.

Adapted from Krypt Trader's momentum scanner
(https://github.com/scripflipped/Krypt-Trader, MIT License). Krypt scores a
Kalshi trade feed by volume spikes, price moves and trade clusters with a tiered
edge model, plus an optional contrarian "fade the crowd" mode. That trade feed
doesn't exist here, so this port keeps the PORTABLE ideas -- tiered momentum
scoring, a volatility-expansion "activity" proxy in place of volume spikes,
breakout confirmation, and the fade option -- computed from the same Alpaca
daily bars the rest of the platform uses.

What it produces: a ranked list of catalysts (symbol, thesis buy/short,
confidence, why). The strongest are ARMED into the existing watch->wait->strike
WatchList, so the bot only strikes when price CONFIRMS the momentum thesis --
turning the scanner into higher-quality entries, not headline chasing.
"""
from __future__ import annotations

import statistics as _stat

UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "NFLX", "AVGO", "CRM", "JPM", "XOM", "COST", "WMT", "ORCL", "ADBE",
]


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) if b else 0.0


def _vol(rets: list[float]) -> float:
    return _stat.pstdev(rets) if len(rets) > 1 else 0.0


def momentum_confidence(*, price_move: float, accel: float, vol_spike: float,
                        breakout: float, trend: float) -> float:
    """Tiered P(up)-like momentum score in [0,1] (adapted from Krypt's tiered
    edge model). Directional base (trend) + additive bonuses for a decisive
    move, acceleration, volatility expansion and a fresh breakout. Callers turn
    this into conviction in whichever direction they trade."""
    base = 50.0 + 40.0 * max(-1.0, min(1.0, trend))     # directional prior
    edge = 0.0
    am = abs(price_move)
    if am >= 0.15:   edge += 14
    elif am >= 0.08: edge += 9
    elif am >= 0.05: edge += 5
    elif am >= 0.03: edge += 2
    aa = abs(accel)
    if aa >= 0.05:   edge += 8
    elif aa >= 0.02: edge += 4
    elif aa >= 0.01: edge += 2
    if vol_spike >= 2.5:  edge += 6
    elif vol_spike >= 1.8: edge += 4
    elif vol_spike >= 1.3: edge += 2
    if breakout >= 0.99:   edge += 8        # at/through recent high or low
    elif breakout >= 0.95: edge += 4
    edge = max(-20.0, min(edge, 30.0))
    return round(max(5.0, min(base + edge, 97.0)) / 100.0, 4)


def _scan_symbol(sym: str, series: list, fade: bool = False) -> dict | None:
    closes = [float(c) for _, c in series]
    if len(closes) < 60:
        return None
    rets = [_pct(closes[i], closes[i - 1]) for i in range(1, len(closes))]
    trend = max(-1.0, min(1.0, _pct(closes[-1], closes[-20]) / 0.10))   # 20d move, scaled
    price_move = _pct(closes[-1], closes[-5])                            # last-week move
    short_ma = _stat.fmean(closes[-5:]); long_ma = _stat.fmean(closes[-20:])
    accel = _pct(short_ma, long_ma)
    recent_vol = _vol(rets[-10:]); base_vol = _vol(rets[-60:-10]) or 1e-9
    vol_spike = recent_vol / base_vol
    hi = max(closes[-20:]); lo = min(closes[-20:])
    up_break = closes[-1] / hi if hi else 0.0
    dn_break = lo / closes[-1] if closes[-1] else 0.0
    thesis = "buy" if trend >= 0 else "short"
    breakout = up_break if thesis == "buy" else dn_break
    pup = momentum_confidence(price_move=price_move, accel=accel, vol_spike=vol_spike,
                              breakout=breakout, trend=trend)
    conf = pup if thesis == "buy" else round(1.0 - pup, 4)   # conviction in thesis
    if fade:                                                  # contrarian: fade the crowd
        thesis = "short" if thesis == "buy" else "buy"
        conf = round(1.0 - conf, 4)
    why = (f"20d {trend*10:+.1f}% · 5d {price_move*100:+.1f}% · accel {accel*100:+.1f}% · "
           f"vol×{vol_spike:.1f}" + (" · breakout" if breakout >= 0.98 else ""))
    return {"symbol": sym, "thesis": thesis, "confidence": conf, "price": round(closes[-1], 4),
            "price_move": round(price_move, 4), "accel": round(accel, 4),
            "vol_spike": round(vol_spike, 2), "breakout": round(breakout, 4), "why": why}


def scan(universe=None, fade: bool = False, min_conf: float = 0.62) -> list[dict]:
    """Rank the universe by momentum-catalyst confidence."""
    from .ml.dataset import _alpaca_series
    out = []
    for sym in (universe or UNIVERSE):
        try:
            ser = _alpaca_series(sym)
        except Exception:  # noqa: BLE001
            ser = []
        r = _scan_symbol(sym, ser, fade=fade) if ser else None
        if r and r["confidence"] >= min_conf:
            out.append(r)
    out.sort(key=lambda r: r["confidence"], reverse=True)
    return out


def arm_top(n: int = 6, fade: bool = False, min_conf: float = 0.66,
            expiry_min: int = 1440, wl=None) -> dict:
    """Scan and ARM the strongest catalysts into the watch->wait->strike list, so
    the bot only strikes when price confirms the momentum thesis. `wl` lets a
    trade loop pass its own WatchList so arming is in-process."""
    cats = scan(fade=fade, min_conf=min_conf)[:n]
    armed = []
    try:
        if wl is None:
            from .watchlist import WatchList
            wl = WatchList()
        for c in cats:
            side = "sell" if c["thesis"] == "short" else "buy"   # broker-side thesis
            entry = wl.arm(c["symbol"], side, c["price"], c["why"],
                           buffer=0.005, expiry_min=expiry_min,
                           confidence=c["confidence"], source="momentum_scanner")
            armed.append({"symbol": c["symbol"], "thesis": side,
                          "confidence": c["confidence"], "trigger": entry.get("trigger")})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160], "scanned": len(cats)}
    try:
        from . import mesh
        if armed:
            top = ", ".join(f"{a['symbol']} {a['thesis']}" for a in armed[:4])
            mesh.publish("desk", "scanner",
                         f"momentum scanner armed {len(armed)} catalysts: {top}", salience=0.6)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "armed": armed, "scanned": len(cats)}


if __name__ == "__main__":
    import json
    print(json.dumps(scan(min_conf=0.0)[:8], indent=2))
