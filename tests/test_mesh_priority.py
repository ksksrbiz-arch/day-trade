"""Hermetic tests for trader.mesh_priority (priority inbox).

We monkeypatch ``trader.mesh.recent`` to return crafted insights so the tests
do not touch the real mesh database.
"""
import time

from trader import mesh, mesh_priority


def _iso(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - seconds_ago))


def _row(id, layer, salience, text, seconds_ago, symbol=""):
    return {
        "id": id, "ts": _iso(seconds_ago), "day": "2026-06-27",
        "layer": layer, "kind": "test", "symbol": symbol,
        "salience": salience, "text": text,
    }


def test_high_recent_outranks_low_old(monkeypatch):
    """A high-salience recent insight beats a low-salience old one."""
    rows = [
        _row("hi", "brain", 0.95, "fresh urgent regime shift", seconds_ago=60),
        _row("lo", "news", 0.10, "stale minor footnote", seconds_ago=48 * 3600),
    ]
    # recent() returns newest-first
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))

    out = mesh_priority.inbox()
    ids = [it["id"] for it in out["items"]]
    assert ids[0] == "hi"
    assert ids.index("hi") < ids.index("lo")
    assert "generated" in out and isinstance(out["generated"], str)


def test_duplicate_text_is_novelty_penalized(monkeypatch):
    """A duplicate (same first-60-char) older item ranks below a unique item of
    equal salience because of the novelty penalty."""
    dup_text = "Market breadth deteriorating across all major sectors today now"
    rows = [
        # newest: the kept copy of the duplicated text
        _row("dup_new", "news", 0.6, dup_text, seconds_ago=30),
        # a unique item of EQUAL salience, slightly older than dup_new
        _row("unique", "brain", 0.6, "Completely different unique insight here", seconds_ago=60),
        # oldest: the duplicate (same first 60 chars) -> penalized
        _row("dup_old", "news", 0.6, dup_text + " EXTRA", seconds_ago=90),
    ]
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))

    out = mesh_priority.inbox()
    ids = [it["id"] for it in out["items"]]
    by_id = {it["id"]: it for it in out["items"]}

    # The older duplicate is penalized, so the unique equal-salience item beats it.
    assert ids.index("unique") < ids.index("dup_old")
    assert by_id["dup_old"]["priority"] < by_id["unique"]["priority"]


def test_counts_returns_ints_high_le_total(monkeypatch):
    rows = [
        _row("a", "brain", 0.95, "very salient very recent", seconds_ago=10),
        _row("b", "news", 0.05, "weak old signal", seconds_ago=72 * 3600),
        _row("c", "ml", 0.8, "decent recent model edge", seconds_ago=120),
    ]
    monkeypatch.setattr(mesh, "recent", lambda **kw: list(rows))

    c = mesh_priority.counts()
    assert isinstance(c["total"], int)
    assert isinstance(c["high"], int)
    assert c["high"] <= c["total"]
    assert c["total"] == 3


def test_empty_is_fail_soft(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda **kw: [])
    out = mesh_priority.inbox()
    assert out["items"] == []
    c = mesh_priority.counts()
    assert c == {"total": 0, "high": 0}
