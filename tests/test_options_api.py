"""Options (OMON) API — mount + keyless-safety, dependency-free.

No TestClient/httpx: assert the route via the OpenAPI schema and call the
handler directly to prove keyless-safety.
"""
from dashboard.app import app


def test_route_mounted():
    assert "/api/options/{symbol}" in set(app.openapi()["paths"])


from dashboard.api.options_api import option_chain, _parse_occ, _group_by_expiry


def test_handler_keyless():
    # Without Alpaca keys the handler must return a structured dict, not raise.
    out = option_chain("AAPL")
    assert isinstance(out, dict)
    assert out["symbol"] == "AAPL"
    assert out["chain"] == []


def test_parse_occ():
    p = _parse_occ("AAPL240119C00195000")
    assert p["expiry"] == "2024-01-19"
    assert p["type"] == "call"
    assert p["strike"] == 195.0
    # garbage in -> safe empty parts, no raise
    assert _parse_occ("garbage")["strike"] is None


def test_group_by_expiry():
    rows = [
        {"expiry": "2024-01-19", "type": "call", "strike": 195.0, "iv": 0.3},
        {"expiry": "2024-01-19", "type": "put", "strike": 195.0, "iv": 0.4},
    ]
    grouped = _group_by_expiry(rows)
    assert grouped[0]["expiry"] == "2024-01-19"
    cell = grouped[0]["strikes"][0]
    assert cell["strike"] == 195.0
    assert cell["call"]["iv"] == 0.3 and cell["put"]["iv"] == 0.4
