"""
Real-time quote hub — the streaming spine for the terminal.

Wraps Alpaca's websocket (`alpaca.data.live.StockDataStream`) and maintains an
in-process cache of the latest top-of-book quote/trade per symbol. A single
background subscriber feeds the cache; HTTP clients read snapshots or subscribe
to a delta feed (see the `/api/quotes` + `/api/quotes/stream` routes).

Design constraints (see docs/ROADMAP.md Phase 0):
- **Keyless-safe.** With no Alpaca credentials the hub simply stays empty and the
  stream degrades to keepalives — the server still boots and serves. Nothing here
  raises on missing keys.
- **Delta-based.** Every update bumps a monotonic version; `changes_since(v)` lets
  the SSE route push only what changed, so streaming does not reintroduce the
  bandwidth we just cut.
- **Lazy + single.** The subscriber thread starts on first use and only once.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

_PROJ = Path(__file__).resolve().parent.parent
_WATCHLIST = _PROJ / "data" / "watchlist.json"

# How many symbols we are willing to subscribe to (IEX free-tier is limited).
_MAX_SYMBOLS = 30


def _seed_symbols() -> list[str]:
    """Symbols to subscribe to: watchlist ∪ open positions ∪ configured universe."""
    syms: set[str] = set()
    # 1) armed watchlist (pure file read, no keys)
    try:
        if _WATCHLIST.exists():
            data = json.loads(_WATCHLIST.read_text() or "{}")
            if isinstance(data, dict):
                syms.update(k.upper() for k in data.keys())
            elif isinstance(data, list):
                syms.update(str(s).upper() for s in data)
    except Exception:  # noqa: BLE001
        pass
    # 2) configured universe
    try:
        from trader import config
        cfg = config.load()
        uni = getattr(cfg.strategy, "universe", None) or []
        syms.update(str(s).upper() for s in uni)
    except Exception:  # noqa: BLE001
        pass
    # 3) open positions (needs keys; best-effort)
    try:
        from trader import config
        from alpaca.trading.client import TradingClient
        cfg = config.load()
        if cfg.alpaca_key and cfg.alpaca_secret:
            tc = TradingClient(cfg.alpaca_key, cfg.alpaca_secret, paper=cfg.alpaca_paper)
            for p in tc.get_all_positions():
                syms.add(p.symbol.upper())
    except Exception:  # noqa: BLE001
        pass
    # crypto pairs (e.g. BTC/USD) are handled by a different stream; keep equities here
    equities = [s for s in syms if "/" not in s]
    return sorted(equities)[:_MAX_SYMBOLS]


class QuoteHub:
    """Singleton cache of latest quotes with a monotonic version for deltas."""

    def __init__(self) -> None:
        self._latest: dict[str, dict] = {}
        self._version = 0
        self._sym_version: dict[str, int] = {}
        self._lock = threading.Lock()
        self._started = False
        self._thread: Optional[threading.Thread] = None
        self._symbols: list[str] = []
        self._stream = None            # live StockDataStream, once running
        self._on_quote = None          # handler refs, for on-demand subscribe
        self._on_trade = None

    # ---- reads -------------------------------------------------------------
    def snapshot(self, symbols: Optional[Iterable[str]] = None) -> dict[str, dict]:
        with self._lock:
            if symbols is None:
                return dict(self._latest)
            want = {s.upper() for s in symbols}
            return {s: q for s, q in self._latest.items() if s in want}

    def changes_since(self, version: int) -> tuple[list[dict], int]:
        """Quotes whose version > `version`, plus the current version."""
        with self._lock:
            if version >= self._version:
                return [], self._version
            out = [self._latest[s] for s, v in self._sym_version.items() if v > version]
            return out, self._version

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def status(self) -> dict:
        with self._lock:
            return {
                "started": self._started,
                "symbols": list(self._symbols),
                "count": len(self._latest),
                "version": self._version,
            }

    # ---- writes ------------------------------------------------------------
    def _update(self, symbol: str, patch: dict) -> None:
        symbol = symbol.upper()
        with self._lock:
            self._version += 1
            q = self._latest.get(symbol, {"symbol": symbol})
            q.update(patch)
            q["ts"] = int(time.time() * 1000)
            self._latest[symbol] = q
            self._sym_version[symbol] = self._version

    # ---- lifecycle ---------------------------------------------------------
    def ensure_started(self) -> None:
        """Start the background subscriber once. No-op if already running."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._symbols = _seed_symbols()
        self._thread = threading.Thread(target=self._run, name="quotehub", daemon=True)
        self._thread.start()

    def ensure_symbol(self, symbol: str) -> None:
        """Subscribe to `symbol` on demand (e.g. a symbol the UI just opened).

        Adds it to the tracked set and, if the live stream is already running,
        best-effort subscribes immediately. Crypto pairs (``BTC/USD``) and
        blanks are ignored. Never raises — a failed live subscribe just means
        the symbol gets picked up on the next reconnect.
        """
        symbol = (symbol or "").upper()
        if not symbol or "/" in symbol:
            return
        with self._lock:
            if symbol in self._symbols:
                return
            self._symbols.append(symbol)
            stream = self._stream
            on_q, on_t = self._on_quote, self._on_trade
        if stream is not None and on_q is not None:
            try:
                stream.subscribe_quotes(on_q, symbol)
                stream.subscribe_trades(on_t, symbol)
                print(f"[quotestream] on-demand subscribe {symbol}")
            except Exception as e:  # noqa: BLE001
                print(f"[quotestream] on-demand subscribe failed {symbol}: {e}")

    def _run(self) -> None:
        try:
            from trader import config
            cfg = config.load()
        except Exception:  # noqa: BLE001
            return
        if not (cfg.alpaca_key and cfg.alpaca_secret):
            # keyless: nothing to subscribe to; hub stays empty (keepalives only)
            print("[quotestream] no Alpaca keys — quote hub idle")
            return
        try:
            from alpaca.data.live import StockDataStream
            from alpaca.data.enums import DataFeed
        except Exception as e:  # noqa: BLE001
            print(f"[quotestream] alpaca live stream unavailable: {e}")
            return

        async def _on_quote(q) -> None:
            try:
                self._update(getattr(q, "symbol", ""), {
                    "bid": float(getattr(q, "bid_price", 0) or 0),
                    "ask": float(getattr(q, "ask_price", 0) or 0),
                    "bid_size": float(getattr(q, "bid_size", 0) or 0),
                    "ask_size": float(getattr(q, "ask_size", 0) or 0),
                })
            except Exception:  # noqa: BLE001
                pass

        async def _on_trade(t) -> None:
            try:
                self._update(getattr(t, "symbol", ""), {
                    "last": float(getattr(t, "price", 0) or 0),
                    "size": float(getattr(t, "size", 0) or 0),
                })
            except Exception:  # noqa: BLE001
                pass

        self._on_quote, self._on_trade = _on_quote, _on_trade

        while True:
            try:
                stream = StockDataStream(cfg.alpaca_key, cfg.alpaca_secret,
                                         feed=DataFeed.IEX)
                with self._lock:
                    syms = list(self._symbols)
                    self._stream = stream
                if syms:
                    stream.subscribe_quotes(_on_quote, *syms)
                    stream.subscribe_trades(_on_trade, *syms)
                print(f"[quotestream] subscribing {len(syms)} symbols")
                stream.run()  # blocks; reconnect on failure
            except Exception as e:  # noqa: BLE001
                print(f"[quotestream] stream error, retrying in 10s: {e}")
                with self._lock:
                    self._stream = None
                time.sleep(10)


# module-level singleton
hub = QuoteHub()
