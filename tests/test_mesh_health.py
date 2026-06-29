"""Hermetic tests for trader.mesh_health (composite mesh-health score).

We monkeypatch the three sibling sources -- ``trader.mesh.recent``,
``trader.mesh_sla.overdue`` and ``trader.mesh_anomaly.anomalies`` -- so the
tests never touch the real mesh DB or any live analytics.
"""
from trader import mesh, mesh_anomaly, mesh_health, mesh_sla


def _row(layer, salience=0.5, text="x"):
    return {
        "id": "%s-x" % layer, "ts": "2026-06-27T00:00:00Z", "day": "2026-06-27",
        "layer": layer, "kind": "test", "symbol": "", "salience": salience,
        "text": text,
    }


def _many_rows(layers, per_layer):
    rows = []
    for lay in layers:
        for _ in range(per_layer):
            rows.append(_row(lay))
    return rows


def test_healthy_case_high_score(monkeypatch):
    """Lots of activity, many layers, nothing stale, no anomalies -> high
    score with an A or B grade."""
    layers = ["brain", "prediction", "ml", "tnet", "news", "cortex",
              "reasoning", "shadow", "consensus", "themes"]
    rows = _many_rows(layers, per_layer=30)  # 300 rows -> activity capped 100
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))
    monkeypatch.setattr(mesh_sla, "overdue", lambda: [])
    monkeypatch.setattr(mesh_anomaly, "anomalies", lambda *a, **kw: [])

    out = mesh_health.score()
    assert out["score"] >= 80
    assert out["grade"] in ("A", "B")
    # all components are healthy
    assert out["components"]["activity"] == 100
    assert out["components"]["diversity"] == 100
    assert out["components"]["sla"] == 100
    assert out["components"]["anomaly"] == 100


def test_degraded_case_lower_score(monkeypatch):
    """Stale layers and warn anomalies drag the score and grade down relative
    to the healthy case."""
    layers = ["brain", "prediction", "ml", "tnet", "news", "cortex",
              "reasoning", "shadow", "consensus", "themes"]
    healthy_rows = _many_rows(layers, per_layer=30)
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(healthy_rows))
    monkeypatch.setattr(mesh_sla, "overdue", lambda: [])
    monkeypatch.setattr(mesh_anomaly, "anomalies", lambda *a, **kw: [])
    healthy = mesh_health.score()

    # now degrade: little activity, few layers, stale + warn anomalies
    sparse_rows = _many_rows(["brain", "ml"], per_layer=5)  # 10 rows, 2 layers
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(sparse_rows))
    monkeypatch.setattr(mesh_sla, "overdue", lambda: [
        {"layer": "news", "status": "stale", "last_seen_min": 200.0},
        {"layer": "tnet", "status": "stale", "last_seen_min": 240.0},
        {"layer": "cortex", "status": "slow", "last_seen_min": 30.0},
    ])
    monkeypatch.setattr(mesh_anomaly, "anomalies", lambda *a, **kw: [
        {"kind": "spike", "severity": "warn", "text": "a"},
        {"kind": "spike", "severity": "warn", "text": "b"},
        {"kind": "drift", "severity": "info", "text": "c"},
    ])
    degraded = mesh_health.score()

    assert degraded["score"] < healthy["score"]
    # grade should be no better than the healthy grade
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    assert order[degraded["grade"]] >= order[healthy["grade"]]
    # sla: 100 - 25*2 - 10*1 = 40 ; anomaly: 100 - 20*2 - 7*1 = 53
    assert degraded["components"]["sla"] == 40
    assert degraded["components"]["anomaly"] == 53


def test_score_bounds_and_shape(monkeypatch):
    """Score is always within 0..100 and all components are present, even with
    pathological (over-saturating) inputs."""
    monkeypatch.setattr(mesh, "recent", lambda **kw: [])
    monkeypatch.setattr(mesh_sla, "overdue", lambda: [
        {"layer": "a", "status": "stale", "last_seen_min": 99.0}
        for _ in range(20)  # would push raw sla far below 0
    ])
    monkeypatch.setattr(mesh_anomaly, "anomalies", lambda *a, **kw: [
        {"kind": "x", "severity": "warn", "text": "z"} for _ in range(20)
    ])

    out = mesh_health.score()
    assert 0 <= out["score"] <= 100
    assert out["grade"] in ("A", "B", "C", "D", "F")
    comps = out["components"]
    assert set(comps.keys()) == {"activity", "diversity", "sla", "anomaly"}
    for v in comps.values():
        assert isinstance(v, int)
        assert 0 <= v <= 100
    assert isinstance(out["generated"], str)
    # floors held
    assert comps["sla"] == 0
    assert comps["anomaly"] == 0
    assert comps["activity"] == 0


def test_summary_one_liner(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(
        _many_rows(["brain", "ml", "news"], per_layer=20)))
    monkeypatch.setattr(mesh_sla, "overdue", lambda: [
        {"layer": "news", "status": "stale", "last_seen_min": 200.0},
    ])
    monkeypatch.setattr(mesh_anomaly, "anomalies", lambda *a, **kw: [])

    s = mesh_health.summary()
    assert isinstance(s, str)
    assert s.startswith("Mesh health ")
    assert "/100" in s
    assert "1 stale layer," in s  # singular
    assert "0 anomalies." in s   # plural
