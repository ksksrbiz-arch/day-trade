"""Tests for the insight mesh (no network; LTM disabled)."""
from trader import mesh


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh, "DB", str(tmp_path / "mesh.db"))
    monkeypatch.setattr(mesh, "_ltm", False)   # disable LTM side-effects


def test_publish_idempotent(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert mesh.publish("brain", "regime", "risk_on baseline", salience=0.7) is True
    assert mesh.publish("brain", "regime", "risk_on baseline", salience=0.7) is False
    assert len(mesh.recent()) == 1


def test_recent_filters(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    mesh.publish("brain", "regime", "x")
    mesh.publish("prediction", "plans", "y AAPL", symbol="AAPL")
    assert len(mesh.recent(layers=["prediction"])) == 1
    assert len(mesh.recent(symbol="AAPL")) == 1


def test_briefing_compiles(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    mesh.publish("brain", "regime", "risk_off stress")
    mesh.publish("ml", "model", "AUC 0.54")
    b = mesh.briefing()
    assert "[brain]" in b and "[ml]" in b


def test_recall_safe_without_ltm(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert mesh.recall("anything") == ""   # no LTM -> empty, no crash
