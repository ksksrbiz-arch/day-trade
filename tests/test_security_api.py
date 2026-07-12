"""Security Master API — mounting + keyless-safety (dependency-free).

No FastAPI TestClient/httpx (not a dependency). We assert the route is present
in the OpenAPI schema (this FastAPI version hides router routes from app.routes)
and call the handler directly to prove it never raises without keys/data.
"""
from dashboard.app import app
from dashboard.api.security import get_security, security_master, _confluence, _mesh


def test_route_mounted():
    assert "/api/security/{symbol}" in set(app.openapi()["paths"])


def test_handler_keyless():
    out = security_master("AAPL")
    assert isinstance(out, dict)
    assert out["symbol"] == "AAPL"
    assert "fundamentals" in out
    assert "house_view" in out


def test_get_security_shape():
    out = get_security("msft")
    assert out["symbol"] == "MSFT"          # normalized upper
    hv = out["house_view"]
    assert set(["confluence", "mesh", "council", "rl"]).issubset(hv.keys())
    # every sub-part is keyless-safe: a dict with an availability flag
    assert isinstance(out["fundamentals"].get("available"), bool)
    assert isinstance(hv["confluence"], dict)
    assert isinstance(hv["mesh"], dict)
    assert isinstance(hv["council"], dict)


def test_empty_symbol_defaults():
    out = get_security("")
    assert out["symbol"] == "AAPL"


def test_subparts_never_raise():
    # partial helpers must swallow all failures and return dicts
    assert isinstance(_confluence("AAPL", None, []), dict)
    assert isinstance(_mesh("AAPL"), dict)
