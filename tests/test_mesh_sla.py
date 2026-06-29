"""Hermetic tests for trader.mesh_sla (per-layer cadence SLA monitor).

We monkeypatch ``trader.mesh.recent`` to return crafted newest-first insight
lists with controlled timestamps so the tests never touch the real mesh DB.
"""
import time

from trader import mesh, mesh_sla


def _iso(epoch: float) -> str:
    """ISO mesh timestamp ("%Y-%m-%dT%H:%M:%SZ", UTC) for an absolute epoch."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _row(id, layer, epoch, salience=0.5, text="x", symbol=""):
    return {
        "id": id, "ts": _iso(epoch), "day": "2026-06-27",
        "layer": layer, "kind": "test", "symbol": symbol,
        "salience": salience, "text": text,
    }


def _rows_every(layer, n, gap_sec, last_offset_sec, now):
    """n insights for `layer`, spaced `gap_sec` apart, the most recent
    `last_offset_sec` before `now`. Returned newest-first."""
    rows = []
    for i in range(n):
        epoch = now - last_offset_sec - i * gap_sec
        rows.append(_row("%s-%d" % (layer, i), layer, epoch))
    return rows  # already newest-first (i=0 is most recent)


def test_on_cadence_layer_is_ok(monkeypatch):
    """A layer publishing every ~1 min with its last insight seconds ago is ok."""
    now = time.time()
    rows = _rows_every("brain", n=10, gap_sec=60, last_offset_sec=5, now=now)
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))

    out = mesh_sla.sla()
    by = {l["layer"]: l for l in out["layers"]}
    assert by["brain"]["status"] == "ok"
    assert by["brain"]["count"] == 10
    assert by["brain"]["median_gap_min"] is not None
    # nothing overdue
    assert mesh_sla.overdue() == []


def test_quiet_but_historically_frequent_is_stale(monkeypatch):
    """A layer that used to publish every minute but has been silent for hours
    is stale and shows up in overdue()."""
    now = time.time()
    # 10 insights one minute apart, but the most recent was 3 hours ago
    rows = _rows_every("news", n=10, gap_sec=60,
                       last_offset_sec=3 * 3600, now=now)
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))

    out = mesh_sla.sla()
    by = {l["layer"]: l for l in out["layers"]}
    assert by["news"]["status"] == "stale"
    # last_seen_min ~ 180, median gap ~ 1 min -> way past 3*gap
    assert by["news"]["last_seen_min"] > 3 * by["news"]["median_gap_min"]

    od = mesh_sla.overdue()
    layers = [o["layer"] for o in od]
    assert "news" in layers
    news_entry = next(o for o in od if o["layer"] == "news")
    assert news_entry["status"] == "stale"
    assert set(news_entry.keys()) == {"layer", "status", "last_seen_min"}


def test_sla_shape_list_and_generated(monkeypatch):
    now = time.time()
    rows = (
        _rows_every("brain", n=5, gap_sec=60, last_offset_sec=5, now=now)
        + _rows_every("ml", n=5, gap_sec=120, last_offset_sec=10, now=now)
    )
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))

    out = mesh_sla.sla()
    assert isinstance(out["layers"], list)
    assert "generated" in out and isinstance(out["generated"], str)
    for l in out["layers"]:
        assert set(["layer", "count", "last_seen_min", "median_gap_min",
                    "expected_min", "status"]).issubset(l.keys())
        assert l["status"] in ("ok", "slow", "stale")


def test_empty_is_fail_soft(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda **kw: [])
    out = mesh_sla.sla()
    assert out["layers"] == []
    assert isinstance(out["generated"], str)
    assert mesh_sla.overdue() == []
