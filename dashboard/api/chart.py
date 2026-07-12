"""Advanced Chart (GP) — OHLC candles + technical studies for the terminal.

Endpoint: ``GET /api/chart/{symbol}?days=120``

Returns daily OHLC candles plus a bundle of overlay/oscillator studies
(SMA, EMA, Bollinger bands, VWAP, RSI, MACD) computed with the shared,
pure ``trader.ta`` indicator math so the terminal renders exactly what the
strategy "sees".

KEYLESS-SAFE: without Alpaca keys (or on any live-data error) every call
returns the structured empty shape ``{"symbol", "candles": [], "studies": {}}``
instead of raising — the panel degrades gracefully, never 500s.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import pstdev

from fastapi import APIRouter

from trader import config, ta

router = APIRouter(prefix="/api/chart", tags=["chart"])

_cfg = config.load()


# --------------------------------------------------------------------------- #
# bars (keyless-safe)                                                          #
# --------------------------------------------------------------------------- #
def _fetch_bars(symbol: str, days: int) -> list[dict]:
    """Daily OHLCV bars oldest->newest, or [] when keys/data are absent.

    Every live path is wrapped: no keys, import failure, or empty response
    all collapse to an empty list so callers never crash.
    """
    if not (_cfg.alpaca_key and _cfg.alpaca_secret):
        return []
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed

        client = StockHistoricalDataClient(_cfg.alpaca_key, _cfg.alpaca_secret)
        # pad the window for weekends/holidays so we get ~`days` trading bars
        start = datetime.now(timezone.utc) - timedelta(days=int(days) * 2 + 15)
        req = StockBarsRequest(
            symbol_or_symbols=symbol.upper(),
            timeframe=TimeFrame.Day,
            start=start,
            feed=DataFeed.IEX,  # free-tier friendly
        )
        data = client.get_stock_bars(req).data.get(symbol.upper(), [])
        out: list[dict] = []
        for b in data:
            t = getattr(b, "timestamp", None)
            out.append({
                "t": t.strftime("%Y-%m-%d") if t else "",
                "o": float(b.open),
                "h": float(b.high),
                "l": float(b.low),
                "c": float(b.close),
                "v": float(b.volume),
            })
        return out[-int(days):] if days and len(out) > int(days) else out
    except Exception as e:  # noqa: BLE001 — never leak a stack trace to the client
        print(f"[chart] bar fetch failed {symbol}: {e}")
        return []


# --------------------------------------------------------------------------- #
# study series (aligned to candles; None where a window is not yet full)       #
# --------------------------------------------------------------------------- #
def _rolling(closes: list[float], fn) -> list[float | None]:
    """Apply ``fn(closes[:i+1])`` at every index -> a value-or-None series."""
    out: list[float | None] = []
    for i in range(len(closes)):
        try:
            v = fn(closes[: i + 1])
        except Exception:  # noqa: BLE001
            v = None
        out.append(round(v, 4) if isinstance(v, (int, float)) else None)
    return out


def _bollinger_bands(closes: list[float], n: int = 20, k: float = 2.0):
    """Upper/mid/lower band series (mid reuses ta.sma for the moving average)."""
    upper: list[float | None] = []
    mid: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(closes)):
        win = closes[: i + 1]
        m = ta.sma(win, n)
        if m is None:
            upper.append(None); mid.append(None); lower.append(None)
            continue
        sd = pstdev(win[-n:])
        upper.append(round(m + k * sd, 4))
        mid.append(round(m, 4))
        lower.append(round(m - k * sd, 4))
    return upper, mid, lower


def _vwap(candles: list[dict]) -> list[float | None]:
    """Cumulative VWAP from typical price ((h+l+c)/3) weighted by volume."""
    out: list[float | None] = []
    cum_pv = 0.0
    cum_v = 0.0
    for b in candles:
        typ = (b["h"] + b["l"] + b["c"]) / 3.0
        cum_pv += typ * b["v"]
        cum_v += b["v"]
        out.append(round(cum_pv / cum_v, 4) if cum_v > 0 else None)
    return out


def _macd_series(closes: list[float]):
    """MACD line/signal/histogram series via ta.macd at each index."""
    line: list[float | None] = []
    signal: list[float | None] = []
    hist: list[float | None] = []
    for i in range(len(closes)):
        try:
            m, s, h = ta.macd(closes[: i + 1])
        except Exception:  # noqa: BLE001
            m = s = h = None
        line.append(round(m, 5) if m is not None else None)
        signal.append(round(s, 5) if s is not None else None)
        hist.append(round(h, 5) if h is not None else None)
    return line, signal, hist


def _build_studies(candles: list[dict]) -> dict:
    closes = [b["c"] for b in candles]
    if not closes:
        return {}
    macd_line, macd_signal, macd_hist = _macd_series(closes)
    bb_upper, bb_mid, bb_lower = _bollinger_bands(closes, 20, 2.0)
    return {
        "sma20": _rolling(closes, lambda xs: ta.sma(xs, 20)),
        "ema12": _rolling(closes, lambda xs: ta.ema(xs, 12)),
        "ema26": _rolling(closes, lambda xs: ta.ema(xs, 26)),
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "vwap": _vwap(candles),
        "rsi14": _rolling(closes, lambda xs: ta.rsi(xs, 14)),
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
    }


# --------------------------------------------------------------------------- #
# route                                                                        #
# --------------------------------------------------------------------------- #
@router.get("/{symbol}")
def get_chart(symbol: str, days: int = 120):
    """OHLC candles + technical studies for ``symbol``.

    ``days`` is clamped to [5, 500]. Callable directly (defaults resolve to
    plain values, not FastAPI ``Query`` sentinels) so tests can prove
    keyless-safety without an HTTP client.

    Keyless/degraded shape: ``{"symbol", "candles": [], "studies": {}}``.
    """
    sym = (symbol or "").upper()
    try:
        days = max(5, min(500, int(days)))
    except (TypeError, ValueError):
        days = 120
    try:
        candles = _fetch_bars(sym, days)
        studies = _build_studies(candles) if candles else {}
        return {"symbol": sym, "days": int(days), "candles": candles, "studies": studies}
    except Exception as e:  # noqa: BLE001 — last-resort guard; never 500
        print(f"[chart] get_chart failed {sym}: {e}")
        return {"symbol": sym, "days": int(days), "candles": [], "studies": {}}
