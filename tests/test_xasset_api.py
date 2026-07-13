"""Cross-Asset (XA) API — mount + shape/offline-safety, dependency-free.

No TestClient/httpx and no reliance on live network: the handler must return the
four-block structure whether or not the upstream feeds respond, and the pure
helpers are checked directly.
"""
from dashboard.app import app
from dashboard.api.xasset import get_xasset, _num, _series_latest


def test_route_mounted():
    assert "/api/xasset" in set(app.openapi()["paths"])


def test_handler_shape():
    out = get_xasset()
    assert isinstance(out, dict)
    for k in ("crypto", "fx", "rates", "commodities"):
        assert k in out
        assert isinstance(out[k], list)


def test_num_helper():
    assert _num(".") is None       # Alpha Vantage missing-value sentinel
    assert _num("") is None
    assert _num(None) is None
    assert _num("3.14") == 3.14


def test_series_latest():
    d = {"data": [{"value": "4.20"}, {"value": "4.10"}, {"value": "."}]}
    latest, chg = _series_latest(d)
    assert latest == 4.20
    assert chg == 0.1
    assert _series_latest({"data": []}) == (None, None)
