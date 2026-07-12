"""Tape & Depth (TAS) API — mount + keyless-safety, dependency-free.

No TestClient/httpx: assert the route via the OpenAPI schema and call the
handler directly to prove it never raises without Alpaca keys.
"""
from dashboard.app import app
from dashboard.api.tape import tape, _book


def test_route_mounted():
    assert "/api/tape/{symbol}" in set(app.openapi()["paths"])


def test_handler_keyless():
    out = tape("AAPL")
    assert isinstance(out, dict)
    assert out["symbol"] == "AAPL"
    assert isinstance(out["prints"], list)
    assert isinstance(out["book"], dict)


def test_handler_blank_symbol():
    out = tape("")
    assert out == {"symbol": "", "prints": [], "book": {}}


def test_book_shape_and_imbalance():
    # empty quote -> empty book
    assert _book({}) == {}
    # bid-stacked book -> positive imbalance in [-1, 1]
    b = _book({"bid": 10.0, "ask": 10.1, "bid_size": 300, "ask_size": 100})
    assert b["bid"] == 10.0 and b["ask"] == 10.1
    assert -1.0 <= b["imbalance"] <= 1.0
    assert b["imbalance"] > 0
