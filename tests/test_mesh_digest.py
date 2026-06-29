"""Tests for the mesh digest markdown composer (hermetic, no real data/ writes)."""
from trader import (
    mesh_digest,
    mesh_narrative,
    mesh_consensus,
    mesh_sla,
    mesh_anomaly,
)


def _patch_full(monkeypatch):
    monkeypatch.setattr(
        mesh_narrative, "narrative",
        lambda: {"text": "The mesh leans bullish across equities."},
    )
    monkeypatch.setattr(
        mesh_consensus, "consensus",
        lambda window=300: {"symbols": [
            {"symbol": "AAPL", "direction": 1, "agree": 4,
             "layers": ["brain", "ml", "tnet", "cortex"]},
        ]},
    )
    monkeypatch.setattr(
        mesh_anomaly, "anomalies",
        lambda window=150: [
            {"kind": "spike", "severity": "warn", "text": "TSLA salience surge"},
        ],
    )
    monkeypatch.setattr(
        mesh_sla, "overdue",
        lambda: [{"layer": "news", "status": "stale", "last_seen_min": 240}],
    )


def _patch_empty(monkeypatch):
    monkeypatch.setattr(mesh_narrative, "narrative", lambda: {"text": ""})
    monkeypatch.setattr(mesh_consensus, "consensus", lambda window=300: {"symbols": []})
    monkeypatch.setattr(mesh_anomaly, "anomalies", lambda window=150: [])
    monkeypatch.setattr(mesh_sla, "overdue", lambda: [])


def test_build_contains_all_sections(monkeypatch):
    _patch_full(monkeypatch)
    md = mesh_digest.build()

    # narrative text
    assert "The mesh leans bullish across equities." in md
    # consensus symbol
    assert "AAPL" in md
    # anomaly
    assert "TSLA salience surge" in md
    # overdue layer
    assert "news" in md and "stale" in md

    # section headers present
    for header in ("## Situation", "## Consensus", "## Anomalies", "## Layer SLA"):
        assert header in md
    assert md.startswith("# Mesh Digest")


def test_write_creates_file_and_readback(tmp_path, monkeypatch):
    _patch_full(monkeypatch)
    target = tmp_path / "digests" / "mesh_latest.md"

    written = mesh_digest.write(str(target))
    assert written == str(target)
    assert target.exists()

    # read back the tmp file we wrote (latest-equivalent)
    content = target.read_text(encoding="utf-8")
    assert "AAPL" in content
    assert "The mesh leans bullish across equities." in content
    assert content.startswith("# Mesh Digest")

    # a timestamped copy should also exist in the same dir
    stamped = list((tmp_path / "digests").glob("mesh_*.md"))
    assert any(p.name != "mesh_latest.md" for p in stamped)


def test_empty_sources_valid_markdown(tmp_path, monkeypatch):
    _patch_empty(monkeypatch)
    md = mesh_digest.build()  # must not raise

    assert "## Consensus" in md
    assert "## Anomalies" in md
    assert "## Layer SLA" in md
    assert "_none_" in md            # consensus + anomalies placeholders
    assert "_all layers nominal_" in md

    # writing the empty digest still works
    target = tmp_path / "mesh_latest.md"
    written = mesh_digest.write(str(target))
    assert written == str(target)
    assert target.exists()
