"""Tests for the Screener (EQS) endpoint — dependency-free (no TestClient)."""
from dashboard.app import app


def test_route_mounted():
    # Router auto-mounts; assert against the OpenAPI schema, not app.routes.
    assert "/api/screen" in set(app.openapi()["paths"])


def test_handler_keyless_safe():
    # Direct call with no Alpaca keys must return a structured dict, never raise.
    from dashboard.api.screen import screen
    out = screen()
    assert isinstance(out, dict)
    assert isinstance(out.get("results"), list)
    assert isinstance(out.get("columns"), list)


def test_handler_filters_shape():
    # Explicit filter args still return the documented shape.
    from dashboard.api.screen import screen
    out = screen(min_score=0.0, min_rvol=1.5, sector="Technology", limit=10)
    assert isinstance(out, dict)
    assert set(out.keys()) >= {"results", "columns"}
