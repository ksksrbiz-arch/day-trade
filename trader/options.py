"""
Paper OPTIONS trading (Alpaca, level 3 verified on this account).

Scope + safety: single-leg, long-only premium (BUY call for bullish, BUY put for
bearish). Long options are defined-risk -- the most you can lose is the debit
paid -- which is the right primitive for an aggressive-but-survivable paper bot.
We do NOT sell naked options here. Everything is PAPER.

The contract-selection rule (pick_contract) is PURE and unit-tested; the network
pieces (listing chains, placing orders) are thin wrappers around alpaca-py.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from typing import Optional


def _type_str(c) -> str:
    t = getattr(c, "type", None)
    return str(getattr(t, "value", t) or "").lower()


def pick_contract(contracts: list, spot: float, side: str):
    """Pure ATM-ish selection.

    side 'buy' -> calls, 'sell' -> puts. Among the matching type, choose the
    nearest expiration, then the strike closest to spot. Returns the contract
    or None.
    """
    want = "call" if side == "buy" else "put"
    pool = [c for c in contracts if want in _type_str(c)]
    if not pool:
        return None
    # nearest expiration first
    def exp(c):
        return str(getattr(c, "expiration_date", ""))
    soonest = min(exp(c) for c in pool)
    pool = [c for c in pool if exp(c) == soonest]
    return min(pool, key=lambda c: abs(float(getattr(c, "strike_price", 0)) - spot))


class OptionsBroker:
    def __init__(self, api_key: str, secret_key: str):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        self.trading = TradingClient(api_key, secret_key, paper=True)
        self.data = StockHistoricalDataClient(api_key, secret_key)

    def spot(self, underlying: str) -> Optional[float]:
        try:
            from alpaca.data.requests import StockLatestTradeRequest
            r = self.data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=underlying))
            return float(r[underlying].price)
        except Exception as e:
            print(f"[options] spot failed {underlying}: {e}")
            return None

    def list_contracts(self, underlying: str, side: str,
                       dte_min: int = 3, dte_max: int = 21, limit: int = 50) -> list:
        try:
            from alpaca.trading.requests import GetOptionContractsRequest
            from alpaca.trading.enums import ContractType
            ct = ContractType.CALL if side == "buy" else ContractType.PUT
            today = date.today()
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying], type=ct,
                expiration_date_gte=today + timedelta(days=dte_min),
                expiration_date_lte=today + timedelta(days=dte_max),
                limit=limit,
            )
            res = self.trading.get_option_contracts(req)
            return list(getattr(res, "option_contracts", res) or [])
        except Exception as e:
            print(f"[options] list contracts failed {underlying}: {e}")
            return []

    def choose(self, underlying: str, side: str):
        spot = self.spot(underlying)
        if spot is None:
            return None, None
        contracts = self.list_contracts(underlying, side)
        return pick_contract(contracts, spot, side), spot

    def buy(self, contract_symbol: str, qty: int = 1) -> Optional[str]:
        """Buy-to-open `qty` contracts at market (DAY). Returns order id."""
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            order = MarketOrderRequest(symbol=contract_symbol, qty=qty,
                                       side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                                       client_order_id=f"{os.getenv('BOT_ID','main')}-{uuid.uuid4().hex[:10]}")
            resp = self.trading.submit_order(order)
            print(f"[options] BUY {qty} {contract_symbol} :: id={resp.id}")
            return str(resp.id)
        except Exception as e:
            print(f"[options] order failed {contract_symbol}: {e}")
            return None

    def option_positions(self) -> list[dict]:
        try:
            out = []
            for p in self.trading.get_all_positions():
                ac = str(getattr(getattr(p, "asset_class", ""), "value", getattr(p, "asset_class", "")))
                if "option" in ac.lower():
                    out.append({"symbol": p.symbol, "qty": float(p.qty),
                                "market_value": float(p.market_value),
                                "unrealized_pl": float(p.unrealized_pl)})
            return out
        except Exception as e:
            print(f"[options] positions failed: {e}")
            return []
