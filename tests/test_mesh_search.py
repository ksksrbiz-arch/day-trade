"""Mesh insight search (hermetic).

Monkeypatch trader.mesh.recent so no DB or live mesh is touched; feed crafted
insights and assert ranked substring search behaves: term filtering, salience-
and recency-aware ordering, empty-query short-circuit, and count() agreement.
"""
import time

from trader import mesh, mesh_search


def _ts(hours_ago: float) -> str:
    """ISO 'YYYY-MM-DDTHH:MM:SSZ' UTC timestamp `hours_ago` in the past."""
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_ago * 3600.0)
    )


def _fake_insights():
    # newest first, mirroring mesh.recent contract
    return [
        {"id": "a", "ts": _ts(0.0), "day": "d", "layer": "news", "kind": "digest",
         "symbol": "NVDA", "salience": 0.5,
         "text": "Nvidia earnings beat, chips rally continues"},
        {"id": "b", "ts": _ts(0.0), "day": "d", "layer": "brain", "kind": "regime",
         "symbol": "NVDA", "salience": 0.9,
         "text": "Nvidia leads the risk-on rotation"},
        {"id": "c", "ts": _ts(48.0), "day": "d", "layer": "tnet", "kind": "forecast",
         "symbol": "NVDA", "salience": 0.9,
         "text": "Nvidia forecast over the next session"},
        {"id": "d", "ts": _ts(0.0), "day": "d", "layer": "reasoning", "kind": "note",
         "symbol": "TSLA", "salience": 0.8,
         "text": "Tesla deliveries soft, demand questions linger"},
    ]


def test_query_returns_only_matching_ranked(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    out = mesh_search.search("nvidia", window=50, limit=20)
    assert out["query"] == "nvidia"
    assert "generated" in out
    ids = [r["id"] for r in out["results"]]
    # only the three nvidia insights match; tesla (d) excluded
    assert set(ids) == {"a", "b", "c"}
    # results are sorted by score descending
    scores = [r["score"] for r in out["results"]]
    assert scores == sorted(scores, reverse=True)
    # b: high salience + fresh outranks c: high salience but 48h old,
    # and a: fresh but lower salience -> b first
    assert ids[0] == "b"


def test_higher_salience_outranks_lower(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    out = mesh_search.search("nvidia", window=50, limit=20)
    by_id = {r["id"]: r["score"] for r in out["results"]}
    # a and b are both fresh (same recency) and both single-term hit;
    # b has higher salience (0.9 vs 0.5) so must score higher
    assert by_id["b"] > by_id["a"]


def test_empty_query_returns_no_results(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    out = mesh_search.search("", window=50)
    assert out["query"] == ""
    assert out["results"] == []
    assert "generated" in out


def test_count_matches_results(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    out = mesh_search.search("nvidia", window=50, limit=20)
    assert mesh_search.count("nvidia", window=50) == len(out["results"])
    assert mesh_search.count("nvidia", window=50) == 3
