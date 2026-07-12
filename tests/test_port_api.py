"""Portfolio Analytics (PORT) API — dependency-free tests.

No TestClient/httpx: we assert the route is mounted via the OpenAPI schema and
call the handler directly to prove it is keyless-safe (returns a structured
dict, never raises, even with no Alpaca account).
"""
from dashboard.app import app
from dashboard.api.port import port


def test_route_mounted():
    assert "/api/port" in set(app.openapi()["paths"])


def test_handler_keyless():
    out = port()
    assert isinstance(out, dict)
    # Stable, structured shape regardless of account availability.
    assert isinstance(out["exposures"], list)
    assert isinstance(out["attribution"], list)
    assert isinstance(out["risk"], dict)


def test_risk_shape_zeroed_safe():
    risk = port()["risk"]
    for key in ("equity", "gross_exposure", "net_exposure", "positions",
                "max_weight", "max_drawdown_pct"):
        assert key in risk
        assert isinstance(risk[key], (int, float))
