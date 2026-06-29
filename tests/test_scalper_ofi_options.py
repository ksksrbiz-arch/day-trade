"""Tests for the scalper, OFI, and options contract-selection logic (all pure)."""
from dataclasses import dataclass

from trader.scalper import bollinger, scalper_signal
from trader.ofi import ofi, ofi_signal, confirms
from trader.options import pick_contract


# ---- scalper / Bollinger ----

def test_bollinger_none_when_short():
    assert bollinger([1, 2, 3], window=20) is None


def test_scalper_buy_below_lower_band():
    closes = [100.0] * 19 + [90.0]      # sharp drop below lower band
    assert scalper_signal(closes, window=20, k=2.0) == "buy"


def test_scalper_sell_above_upper_band():
    closes = [100.0] * 19 + [110.0]     # spike above upper band
    assert scalper_signal(closes, window=20, k=2.0) == "sell"


def test_scalper_none_inside_band():
    closes = [100.0, 101.0, 99.0, 100.5, 100.0] * 4   # tame, inside band
    assert scalper_signal(closes, window=20, k=2.0) is None


# ---- OFI ----

def test_ofi_balanced_zero():
    assert ofi(100, 100) == 0.0


def test_ofi_buy_pressure_positive():
    assert ofi(500, 100) > 0.5


def test_ofi_signal_thresholds():
    assert ofi_signal(0.7, 0.6) == "buy"
    assert ofi_signal(-0.7, 0.6) == "sell"
    assert ofi_signal(0.2, 0.6) is None


def test_ofi_confirms_sign():
    assert confirms("buy", 0.3) is True
    assert confirms("buy", -0.3) is False
    assert confirms("sell", -0.3) is True


# ---- options contract selection ----

@dataclass
class C:
    symbol: str
    strike_price: float
    expiration_date: str
    type: str


CHAIN = [
    C("AAPLc1", 220, "2026-06-26", "call"),
    C("AAPLc2", 230, "2026-06-26", "call"),  # closest to spot 232 on soonest exp
    C("AAPLc3", 230, "2026-07-03", "call"),  # later expiration
    C("AAPLp1", 230, "2026-06-26", "put"),
    C("AAPLp2", 235, "2026-06-26", "put"),
]


def test_pick_call_atm_nearest_expiry():
    c = pick_contract(CHAIN, spot=232.0, side="buy")
    assert c.symbol == "AAPLc2"   # call, soonest expiry, strike nearest 232


def test_pick_put_for_short():
    c = pick_contract(CHAIN, spot=232.0, side="sell")
    assert _t(c) == "put" and c.expiration_date == "2026-06-26"


def test_pick_none_when_no_type():
    only_calls = [c for c in CHAIN if c.type == "call"]
    assert pick_contract(only_calls, 232.0, "sell") is None


def _t(c):
    return c.type
