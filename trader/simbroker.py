"""
SimBroker: a paper/backtest broker that ALWAYS fills you at a worse price than
you wanted. This exists to counteract the single most dangerous lie in this
whole project -- that paper trading flatters you.

Alpaca's own paper environment fills generously (it won't even check your order
against real available liquidity). A strategy that looks profitable on naive
paper fills can be a guaranteed loser live, once spread + slippage + fees are
paid on every entry and exit. So in backtests we model the cost explicitly and
make every fill hurt.

slippage_bps = 10 means each fill moves 0.10% against you. On a round trip
(enter + exit) that's ~0.20% you must overcome before you've made a cent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SimConfig:
    slippage_bps: float = 10.0   # per fill, against you
    fee_bps: float = 0.0         # per fill (e.g. crypto ~15-25 bps)
    starting_cash: float = 100.0


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float   # the price you ACTUALLY got, after haircut
    side: str            # "long" | "short"


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float

    @property
    def pnl(self) -> float:
        gross = (self.exit_price - self.entry_price) * self.qty
        return gross if self.side == "long" else -gross


def _haircut(price: float, side_is_buy: bool, cfg: SimConfig) -> float:
    """Worse price for you: buys fill higher, sells fill lower."""
    slip = cfg.slippage_bps / 10_000.0
    fee = cfg.fee_bps / 10_000.0
    adj = slip + fee
    return price * (1 + adj) if side_is_buy else price * (1 - adj)


class SimBroker:
    def __init__(self, cfg: Optional[SimConfig] = None):
        self.cfg = cfg or SimConfig()
        self.cash = self.cfg.starting_cash
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.equity_curve: list[float] = [self.cfg.starting_cash]

    def open(self, symbol: str, notional: float, ref_price: float, side: str) -> Position:
        is_buy = side == "long"
        fill = _haircut(ref_price, is_buy, self.cfg)
        qty = notional / fill
        self.cash -= notional
        pos = Position(symbol=symbol, qty=qty, entry_price=fill, side=side)
        self.positions[symbol] = pos
        return pos

    def close(self, symbol: str, ref_price: float) -> Optional[ClosedTrade]:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None
        # closing a long = sell; closing a short = buy
        is_buy = pos.side == "short"
        fill = _haircut(ref_price, is_buy, self.cfg)
        proceeds = fill * pos.qty
        self.cash += proceeds if pos.side == "long" else (2 * pos.entry_price * pos.qty - proceeds)
        trade = ClosedTrade(
            symbol=symbol, side=pos.side, qty=pos.qty,
            entry_price=pos.entry_price, exit_price=fill,
        )
        self.closed.append(trade)
        self.equity_curve.append(self.equity())
        return trade

    def equity(self, marks: Optional[dict[str, float]] = None) -> float:
        marks = marks or {}
        held = 0.0
        for sym, pos in self.positions.items():
            mark = marks.get(sym, pos.entry_price)
            held += mark * pos.qty if pos.side == "long" else \
                (2 * pos.entry_price - mark) * pos.qty
        return self.cash + held
