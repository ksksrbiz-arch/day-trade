"""Hermetic tests for trader.mesh_narrative.

The three upstream sources (consensus, themes, anomalies) are monkeypatched so
the narrative synthesizer is exercised against crafted inputs with no mesh,
model, or network involved.
"""
from trader import mesh_narrative
from trader import mesh_consensus, mesh_themes, mesh_anomaly


def test_narrative_with_all_sources(monkeypatch):
    monkeypatch.setattr(
        mesh_consensus, "consensus",
        lambda window=300: {
            "symbols": [
                {"symbol": "AAPL", "direction": "up", "agree": 3,
                 "net": 0.42, "layers": ["a", "b", "c"]},
                {"symbol": "TSLA", "direction": "down", "agree": 2,
                 "net": -0.30, "layers": ["a", "b"]},
            ],
            "generated": "now",
        },
    )
    monkeypatch.setattr(
        mesh_themes, "themes",
        lambda window=200, top=8: {
            "themes": [
                {"term": "earnings", "count": 9, "weight": 4.1, "layers": ["x"]},
                {"term": "rotation", "count": 5, "weight": 2.0, "layers": ["y"]},
            ],
            "generated": "now",
        },
    )
    monkeypatch.setattr(
        mesh_anomaly, "anomalies",
        lambda window=150: [
            {"kind": "volume_burst", "severity": "info", "text": "minor burst"},
            {"kind": "salience_spike", "severity": "warn",
             "text": "Recent salience jumped to 0.90"},
        ],
    )

    out = mesh_narrative.narrative()
    text = out["text"]

    # Top symbol named.
    assert "AAPL" in text
    # Theme term present.
    assert "earnings" in text
    # Most severe anomaly (warn beats info) summarized.
    assert "salience jumped" in text

    assert out["parts"] == {"consensus": 2, "themes": 2, "anomalies": 2}
    assert isinstance(out["generated"], str) and out["generated"]


def test_narrative_all_empty(monkeypatch):
    monkeypatch.setattr(
        mesh_consensus, "consensus",
        lambda window=300: {"symbols": [], "generated": "now"},
    )
    monkeypatch.setattr(
        mesh_themes, "themes",
        lambda window=200, top=8: {"themes": [], "generated": "now"},
    )
    monkeypatch.setattr(
        mesh_anomaly, "anomalies",
        lambda window=150: [],
    )

    out = mesh_narrative.narrative()  # must not raise
    text = out["text"]

    assert "No multi-layer consensus" in text
    assert "No anomalies detected." in text
    assert out["parts"] == {"consensus": 0, "themes": 0, "anomalies": 0}
