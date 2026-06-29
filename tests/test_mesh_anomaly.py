"""Mesh anomaly detection (hermetic).

Monkeypatches ``trader.mesh.recent`` with crafted, newest-first lists so the
tests never touch the real mesh DB or wall-clock-dependent data.
"""
import time

from trader import mesh, mesh_anomaly


def _iso(epoch):
    # Format with localtime so it round-trips through the module's
    # strptime+mktime (local-time) parsing, keeping ordering unambiguous.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(epoch))


def _row(i, layer="brain", salience=0.5, age_sec=0, kind="note", symbol="SPY"):
    ts = _iso(time.time() - age_sec)
    return {
        "id": i,
        "ts": ts,
        "day": ts[:10],
        "layer": layer,
        "kind": kind,
        "symbol": symbol,
        "salience": salience,
        "text": "insight %d" % i,
    }


def _patch(monkeypatch, rows):
    monkeypatch.setattr(mesh, "recent", lambda n=30, layers=None, symbol="": list(rows))


def test_salience_spike(monkeypatch):
    # newest-first: 4 recent high-salience, 10 older low-salience.
    recent = [_row(i, salience=0.9, age_sec=60 + i) for i in range(4)]
    older = [_row(100 + i, salience=0.3, age_sec=3600 + i * 60) for i in range(10)]
    _patch(monkeypatch, recent + older)
    out = mesh_anomaly.anomalies(window=150)
    kinds = [a["kind"] for a in out]
    assert "salience_spike" in kinds
    spike = next(a for a in out if a["kind"] == "salience_spike")
    assert spike["severity"] == "warn"


def test_layer_silence(monkeypatch):
    # "news" appears only in the older portion -> layer_silence.
    recent = [_row(i, layer="brain", salience=0.5, age_sec=60 + i) for i in range(4)]
    older = (
        [_row(100 + i, layer="news", salience=0.5, age_sec=3600 + i * 60) for i in range(3)]
        + [_row(200 + i, layer="brain", salience=0.5, age_sec=4000 + i * 60) for i in range(7)]
    )
    _patch(monkeypatch, recent + older)
    out = mesh_anomaly.anomalies(window=150)
    silences = [a for a in out if a["kind"] == "layer_silence"]
    assert any(a["layer"] == "news" for a in silences)
    news = next(a for a in silences if a["layer"] == "news")
    assert news["severity"] == "info"  # only 3 older insights -> info, not warn


def test_too_few_returns_empty(monkeypatch):
    _patch(monkeypatch, [_row(i) for i in range(5)])
    assert mesh_anomaly.anomalies(window=150) == []


def test_summary_shape(monkeypatch):
    _patch(monkeypatch, [_row(i) for i in range(3)])
    s = mesh_anomaly.summary()
    assert set(s.keys()) == {"count", "anomalies"}
    assert s["count"] == 0 and s["anomalies"] == []
