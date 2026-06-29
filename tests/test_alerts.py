"""Tests for the alerts engine (idempotency, rules, ack; hermetic)."""
from trader import alerts


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(alerts, "STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(alerts, "_DATA", str(tmp_path))


def _stub_quiet(monkeypatch):
    from trader.agents import state as ag
    import trader.market_brain as mb
    import trader.autonomy as au
    import trader.edge as ed
    monkeypatch.setattr(ag, "kv_get", lambda k, d=None: {})
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "neutral")
    monkeypatch.setattr(au, "recent_audit", lambda n=10: [])
    monkeypatch.setattr(ed, "report", lambda *a, **k: {"sources": []})
    import trader.mesh_anomaly as ma
    import trader.mesh_sla as ms
    monkeypatch.setattr(ma, "anomalies", lambda *a, **k: [])
    monkeypatch.setattr(ms, "overdue", lambda *a, **k: [])


def test_put_idempotent(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = alerts._conn()
    a1 = alerts._put(c, "x", "warn", "hello", "k")
    a2 = alerts._put(c, "x", "warn", "hello", "k")
    c.commit(); c.close()
    assert a1 and a2 is None                 # second is a dup
    assert alerts.counts()["unack"] == 1


def test_fire_service_down_and_dedup(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    _stub_quiet(monkeypatch)
    from trader.agents import state as ag
    monkeypatch.setattr(ag, "kv_get", lambda k, d=None:
                        {"services": {"dashboard": False}, "selftest": {"ok": True, "fails": []}}
                        if k == "system_health" else d)
    r1 = alerts.fire()
    assert any(a["severity"] == "critical" and "dashboard" in a["text"] for a in r1["alerts"])
    assert alerts.fire()["new"] == 0          # same condition same day -> no repeat


def test_fire_regime_change(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    _stub_quiet(monkeypatch)
    import trader.market_brain as mb
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "neutral")
    alerts.fire()                              # establish baseline (no regime alert)
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "high_vol")
    r = alerts.fire()
    assert any(a["kind"] == "regime" and "high_vol" in a["text"] for a in r["alerts"])


def test_ack(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = alerts._conn(); alerts._put(c, "x", "warn", "t", "k"); c.commit(); c.close()
    assert alerts.counts()["unack"] == 1
    alerts.ack("all")
    assert alerts.counts()["unack"] == 0
