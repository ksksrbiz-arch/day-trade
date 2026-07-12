"""Market Monitor (MOST) API — mount + keyless-safety, dependency-free."""
from dashboard.app import app
from dashboard.api.monitor import monitor, get_monitor, _sector


def test_route_mounted():
    assert "/api/monitor" in set(app.openapi()["paths"])


def test_handler_keyless():
    out = monitor()
    assert isinstance(out, dict)
    for k in ("movers", "most_active", "breadth", "sectors"):
        assert k in out
    assert set(out["movers"].keys()) == {"gainers", "losers"}
    b = out["breadth"]
    assert set(["advancers", "decliners", "adv_pct", "total"]).issubset(b.keys())


def test_get_monitor_shape_and_limit():
    out = get_monitor(limit=3)
    assert isinstance(out["most_active"], list)
    assert isinstance(out["sectors"], list)


def test_sector_map():
    assert _sector("AAPL") == "Technology"
    assert _sector("ZZZZ") == "Other"
