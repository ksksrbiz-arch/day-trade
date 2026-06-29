"""
Deterministic technical features per ticker.

The feature math (`compute_features`) is a PURE function of a price/volume
history -- same input, same output, no network, no randomness -- so it's
unit-testable and backtestable, exactly like strategy.decide().

Two data backends feed it:
  * Alpaca daily bars  -> per-symbol, works live with the existing keys (default)
  * Massive flat files -> whole-market bulk, drop-in for backtests (entitlement
                          pending; falls back to Alpaca automatically)

Features are intentionally simple and auditable: trailing returns (momentum),
realized volatility, relative volume, and trend vs a 20-day SMA.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from statistics import pstdev, fmean
from typing import Optional


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


@dataclass
class Features:
    symbol: str
    n_days: int
    last_close: float
    ret_5d: float          # trailing 5-trading-day return
    ret_20d: float         # trailing 20-trading-day return
    vol_20d: float         # stdev of daily returns over ~20d (realized vol)
    rvol: float            # latest volume / 20d average volume
    above_sma20: bool      # last close above its 20-day simple moving average
    source: str = "alpaca"

    def as_log(self) -> dict:
        d = asdict(self)
        d["above_sma20"] = int(self.above_sma20)
        return d


def compute_features(symbol: str, closes: list[float], volumes: list[float], source: str = "alpaca") -> Optional[Features]:
    """Pure feature computation from oldest->newest daily closes/volumes."""
    n = len(closes)
    if n < 6 or len(volumes) != n:
        return None
    last = closes[-1]

    def trailing_ret(k: int) -> float:
        if n <= k or closes[-1 - k] == 0:
            return 0.0
        return (last / closes[-1 - k]) - 1.0

    ret_5d = trailing_ret(5)
    ret_20d = trailing_ret(min(20, n - 1))

    # daily returns for realized vol (last ~20)
    rets = []
    window = closes[-21:] if n >= 21 else closes
    for i in range(1, len(window)):
        prev = window[i - 1]
        if prev:
            rets.append(window[i] / prev - 1.0)
    vol_20d = pstdev(rets) if len(rets) >= 2 else 0.0

    vwindow = volumes[-20:] if n >= 20 else volumes
    avg_vol = fmean(vwindow) if vwindow else 0.0
    rvol = (volumes[-1] / avg_vol) if avg_vol > 0 else 1.0

    smawin = closes[-20:] if n >= 20 else closes
    sma20 = fmean(smawin)
    above = last >= sma20

    return Features(
        symbol=symbol, n_days=n, last_close=round(last, 4),
        ret_5d=round(ret_5d, 4), ret_20d=round(ret_20d, 4),
        vol_20d=round(vol_20d, 4), rvol=round(rvol, 3),
        above_sma20=above, source=source,
    )


class MarketData:
    """Feature provider. Prefers Massive flat files when downloadable, else Alpaca."""

    def __init__(self, alpaca_key: str, alpaca_secret: str, massive=None):
        self.massive = massive
        self._alpaca = None
        self._crypto = None
        self._akey = alpaca_key
        self._asec = alpaca_secret

    def _alpaca_client(self):
        if self._alpaca is None:
            from alpaca.data.historical import StockHistoricalDataClient
            self._alpaca = StockHistoricalDataClient(self._akey, self._asec)
        return self._alpaca

    def _crypto_client(self):
        if self._crypto is None:
            from alpaca.data.historical import CryptoHistoricalDataClient
            self._crypto = CryptoHistoricalDataClient(self._akey, self._asec)
        return self._crypto

    def _crypto_bars(self, symbol: str, hours: int = 80):
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame
        start = datetime.now(timezone.utc) - timedelta(hours=hours + 5)
        req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start)
        data = self._crypto_client().get_crypto_bars(req).data.get(symbol, [])
        return [float(b.close) for b in data], [float(b.volume) for b in data]

    def _alpaca_bars(self, symbol: str, lookback_days: int = 40):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
        start = datetime.now(timezone.utc) - timedelta(days=lookback_days + 10)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            feed=DataFeed.IEX,   # free-tier friendly
        )
        bars = self._alpaca_client().get_stock_bars(req)
        data = bars.data.get(symbol, [])
        closes = [float(b.close) for b in data]
        vols = [float(b.volume) for b in data]
        return closes, vols

    def recent_closes(self, symbol: str, lookback_days: int = 60) -> list[float]:
        try:
            if is_crypto(symbol):
                return self._crypto_bars(symbol)[0]
            closes, _ = self._alpaca_bars(symbol.upper(), lookback_days=lookback_days)
            return closes
        except Exception as e:
            print(f"[marketdata] recent_closes failed {symbol}: {e}")
            return []

    def ofi(self, symbol: str) -> Optional[float]:
        """Top-of-book order-flow imbalance from the latest quote, or None."""
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            from alpaca.data.enums import DataFeed
            from .ofi import ofi as _ofi
            if is_crypto(symbol):
                from alpaca.data.requests import CryptoLatestQuoteRequest
                cq = self._crypto_client().get_crypto_latest_quote(CryptoLatestQuoteRequest(symbol_or_symbols=symbol))[symbol]
                return _ofi(float(cq.bid_size or 0), float(cq.ask_size or 0))
            r = self._alpaca_client().get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbol.upper(), feed=DataFeed.IEX))
            q = r[symbol.upper()]
            return _ofi(float(q.bid_size or 0), float(q.ask_size or 0))
        except Exception as e:
            print(f"[marketdata] ofi failed {symbol}: {e}")
            return None

    def features(self, symbol: str) -> Optional[Features]:
        symbol = symbol.upper()
        if is_crypto(symbol):
            try:
                cl, vl = self._crypto_bars(symbol)
                return compute_features(symbol, cl, vl, source="crypto")
            except Exception as e:
                print(f"[marketdata] crypto features failed {symbol}: {e}")
                return None
        # Massive bulk path only when the account can actually download.
        if self.massive is not None and self.massive.can_download():
            try:
                closes, vols = self._massive_history(symbol)
                f = compute_features(symbol, closes, vols, source="massive")
                if f:
                    return f
            except Exception:
                pass
        # Live default: Alpaca per-symbol bars.
        try:
            closes, vols = self._alpaca_bars(symbol)
            return compute_features(symbol, closes, vols, source="alpaca")
        except Exception as e:
            print(f"[marketdata] feature fetch failed for {symbol}: {e}")
            return None

    def _massive_history(self, symbol: str, days: int = 30):
        """Assemble ~`days` recent daily closes for one symbol from flat files."""
        closes, vols = [], []
        d = datetime.now(timezone.utc).date()
        collected = 0
        scanned = 0
        while collected < days and scanned < days * 2 + 10:
            ds = d.isoformat()
            csv_text = self.massive.day_aggs_csv(ds)
            scanned += 1
            d = d - timedelta(days=1)
            if not csv_text:
                continue
            row = self.massive.parse_day_aggs(csv_text).get(symbol)
            if row:
                closes.append(row["close"])
                vols.append(row["volume"])
                collected += 1
        closes.reverse(); vols.reverse()
        return closes, vols
