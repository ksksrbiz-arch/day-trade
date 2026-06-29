"""Tests for supervisor self-repair: crash-loop backoff + selftest."""
from trader.agents import supervisor as sup


def _kv(monkeypatch):
    store = {}
    monkeypatch.setattr(sup.state, "kv_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(sup.state, "kv_set", lambda k, v: store.__setitem__(k, v))
    return store


def test_crash_loop_backoff(monkeypatch):
    _kv(monkeypatch)
    assert sup._crash_looping("svc") is False
    for _ in range(sup._CRASH_MAX):
        sup._record_restart("svc")
    assert sup._crash_looping("svc") is True       # too many restarts -> back off
    assert sup._crash_looping("other") is False     # isolated per service


def test_selftest_structure(monkeypatch):
    _kv(monkeypatch)
    import trader.mesh as mesh
    monkeypatch.setattr(mesh, "publish", lambda *a, **k: None)
    r = sup.selftest()
    assert set(["ok", "fails", "checks"]).issubset(r)
    assert r["checks"]["trader.alpha"] == "ok"      # core import resolves
    assert isinstance(r["fails"], list)
