"""
Live(-ish) broker. Talks to Alpaca's PAPER endpoint by default.

Exits are delegated to Alpaca via bracket orders: every entry ships with a
take-profit and a stop-loss attached, so you don't need a separate exit-manager
loop in v0 and a crash won't leave a naked runaway position.

IMPORTANT: paper=True is the default and you should keep it there until a
properly slippage-haircut backtest over weeks shows an edge that beats SPY.
"""
from __future__ import annotations

import os
import uuid
from .marketdata import is_crypto

from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

from .strategy import Intent


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.data = StockHistoricalDataClient(api_key, secret_key)

    def account_value(self) -> float:
        acct = self.trading.get_account()
        return float(acct.equity)

    def open_symbols(self) -> set[str]:
        return {p.symbol for p in self.trading.get_all_positions()}

    def last_price(self, symbol: str) -> Optional[float]:
        try:
            req = StockLatestTradeRequest(symbol_or_symbols=symbol)
            trade = self.data.get_stock_latest_trade(req)[symbol]
            return float(trade.price)
        except Exception as e:
            print(f"[broker] price lookup failed for {symbol}: {e}")
            return None

    def submit(self, intent: Intent) -> Optional[str]:
        """Submit a bracket market order (equity) or notional market (crypto)."""
        if is_crypto(intent.symbol):
            return self._submit_crypto(intent)
        price = self.last_price(intent.symbol)
        if price is None:
            return None

        if os.getenv("EXTENDED_HOURS", "false").lower() in ("1", "true", "yes", "on"):
            return self._submit_equity_extended(intent, price)
        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        if side == OrderSide.BUY:
            tp = round(price * (1 + intent.take_profit_pct), 2)
            sl = round(price * (1 - intent.stop_loss_pct), 2)
        else:
            tp = round(price * (1 - intent.take_profit_pct), 2)
            sl = round(price * (1 + intent.stop_loss_pct), 2)

        # bracket orders require whole-share qty (no notional), so size here
        qty = max(1, int(intent.notional // price))

        order = MarketOrderRequest(
            symbol=intent.symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp),
            stop_loss=StopLossRequest(stop_price=sl),
            client_order_id=f"{os.getenv('BOT_ID','main')}-{uuid.uuid4().hex[:10]}",
        )
        try:
            resp = self.trading.submit_order(order)
            print(f"[broker] {intent.side} {qty} {intent.symbol} @~{price} "
                  f"(tp={tp} sl={sl}) :: {intent.reason}")
            return str(resp.id)
        except Exception as e:
            print(f"[broker] order failed for {intent.symbol}: {e}")
            return None

    def cancel_all_orders(self) -> int:
        """Cancel every open order (used by the daily circuit breaker)."""
        try:
            res = self.trading.cancel_orders()
            return len(res) if res else 0
        except Exception as e:
            print(f"[broker] cancel_all failed: {e}")
            return 0

    def positions_detailed(self) -> list[dict]:
        """Open positions with entry + current price, for the exits manager."""
        out = []
        try:
            for p in self.trading.get_all_positions():
                qty = float(p.qty)
                ac = str(getattr(getattr(p, "asset_class", ""), "value", getattr(p, "asset_class", "")))
                out.append({
                    "symbol": p.symbol,
                    "qty": qty,
                    "side": "buy" if qty >= 0 else "sell",
                    "avg_entry": float(p.avg_entry_price),
                    "current": float(p.current_price) if p.current_price else None,
                    "unrealized_plpc": (float(p.unrealized_plpc) if getattr(p, "unrealized_plpc", None) is not None else None),
                    "asset_class": ac,
                })
        except Exception as e:
            print(f"[broker] positions_detailed failed: {e}")
        return out

    def _cancel_symbol_orders(self, symbol: str) -> int:
        """Cancel a symbol's OPEN orders so its shares aren't held_for_orders --
        otherwise close_position is rejected with 'insufficient qty available'
        (the bracket TP/SL holds the shares). Best-effort, fail-soft."""
        n = 0
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            oo = self.trading.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol]))
            for o in oo:
                try:
                    self.trading.cancel_order_by_id(o.id); n += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return n

    def close_position(self, symbol: str) -> bool:
        try:
            self._cancel_symbol_orders(symbol)
            self.trading.close_position(symbol)
            return True
        except Exception as e:
            print(f"[broker] close_position {symbol} failed: {e}")
            return False

    def _submit_crypto(self, intent) -> "str | None":
        """Crypto: simple notional market order, GTC, 24/7. No bracket (crypto
        doesn't support it) -- the exits manager handles trailing exits."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        try:
            order = MarketOrderRequest(
                symbol=intent.symbol, notional=round(intent.notional, 2),
                side=side, time_in_force=TimeInForce.GTC,
                client_order_id=f"{os.getenv('BOT_ID','main')}-{uuid.uuid4().hex[:10]}",
            )
            resp = self.trading.submit_order(order)
            print(f"[broker] crypto {intent.side} ${intent.notional:.0f} {intent.symbol} :: {intent.reason}")
            return str(resp.id)
        except Exception as e:
            print(f"[broker] crypto order failed {intent.symbol}: {e}")
            return None

    def _submit_equity_extended(self, intent, price) -> "str | None":
        """Extended-hours equity order: marketable LIMIT + extended_hours=True
        (DAY). No bracket in ext-hours -- the exits manager handles stops. Works
        in regular hours too (just a marketable limit)."""
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        buf = 0.003
        limit = round(price * (1 + buf), 2) if side == OrderSide.BUY else round(price * (1 - buf), 2)
        qty = max(1, int(intent.notional // price))
        try:
            order = LimitOrderRequest(
                symbol=intent.symbol, qty=qty, side=side,
                time_in_force=TimeInForce.DAY, limit_price=limit, extended_hours=True,
                client_order_id=f"{os.getenv('BOT_ID','main')}-{uuid.uuid4().hex[:10]}",
            )
            resp = self.trading.submit_order(order)
            print(f"[broker] EXT-HRS {intent.side} {qty} {intent.symbol} lim={limit} :: {intent.reason}")
            return str(resp.id)
        except Exception as e:
            print(f"[broker] ext-hrs order failed {intent.symbol}: {e}")
            return None
