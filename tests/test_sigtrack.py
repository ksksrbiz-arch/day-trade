"""Tests for the live signal scorecard (no network)."""
from trader import sigtrack


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sigtrack, "DB", str(tmp_path / "sig.db"))


def test_record_and_scoreboard(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert sigtrack.record("confluence", "AAPL", "buy", 200.0, 0.4) is True
    assert sigtrack.record("ml", "XOM", "sell", 110.0, -0.3) is True
    sb = sigtrack.scoreboard()["by_source"]
    srcs = {r["source"]: r for r in sb}
    assert srcs["confluence"]["signals"] == 1 and srcs["ml"]["signals"] == 1
    assert srcs["confluence"]["resolved"] == 0


def test_dedup_same_day(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert sigtrack.record("ml", "AAPL", "buy", 200.0) is True
    assert sigtrack.record("ml", "AAPL", "buy", 201.0) is False   # same src/sym/side/day


def test_rejects_bad_side(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert sigtrack.record("ml", "AAPL", "hold", 200.0) is False
    assert sigtrack.record("ml", "", "buy", 200.0) is False


def test_scoreboard_empty(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert sigtrack.scoreboard() == {"by_source": []}
