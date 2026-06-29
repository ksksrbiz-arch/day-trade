"""Hermetic tests for trader.mesh_cluster symbol clustering."""
import time

from trader import mesh, mesh_cluster


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _insight(epoch, layer, symbol, salience=0.6):
    return {
        "id": f"{layer}-{symbol}-{epoch}",
        "ts": _iso(epoch),
        "day": time.strftime("%Y-%m-%d", time.gmtime(epoch)),
        "layer": layer,
        "kind": "test",
        "symbol": symbol,
        "salience": salience,
        "text": f"{layer} on {symbol}",
    }


def _patch(monkeypatch, insights):
    # mesh.recent returns NEWEST FIRST -> reverse chronological.
    ordered = sorted(insights, key=lambda r: r["ts"], reverse=True)

    def fake_recent(n=30, layers=None, symbol=""):
        return ordered[:n]

    monkeypatch.setattr(mesh, "recent", fake_recent)


def _tech_mesh(base=1_700_000_000):
    """AAPL/MSFT/NVDA mentioned together by the same layers; ZZZ alone."""
    insights = []
    e = base
    # Several layers each mention the tech trio together -> high co-occurrence.
    for layer in ("tnet", "ml", "news", "council"):
        for sym in ("AAPL", "MSFT", "NVDA"):
            insights.append(_insight(e, layer, sym))
            e += 60
    # An unrelated symbol mentioned once, by a layer that mentions nothing else.
    insights.append(_insight(e, "solo", "ZZZ"))
    return insights


def test_tech_cluster_emerges(monkeypatch):
    _patch(monkeypatch, _tech_mesh())
    out = mesh_cluster.clusters(window=400, min_size=2)
    assert "generated" in out
    cls = out["clusters"]
    assert cls, f"expected at least one cluster, got {cls}"

    tech = [c for c in cls
            if {"AAPL", "MSFT", "NVDA"}.issubset(set(c["symbols"]))]
    assert tech, f"expected AAPL/MSFT/NVDA cluster, got {cls}"
    cluster = tech[0]
    assert cluster["size"] >= 2
    assert cluster["strength"] > 0


def test_related_includes_co_mentioned(monkeypatch):
    _patch(monkeypatch, _tech_mesh())
    rel = mesh_cluster.related("AAPL", window=400, top=5)
    syms = {r["symbol"] for r in rel}
    assert "MSFT" in syms, rel
    assert "NVDA" in syms, rel
    for r in rel:
        assert r["weight"] > 0


def test_lone_symbol_not_clustered(monkeypatch):
    _patch(monkeypatch, _tech_mesh())
    out = mesh_cluster.clusters(window=400, min_size=2)
    for c in out["clusters"]:
        assert "ZZZ" not in c["symbols"], f"lone symbol clustered: {c}"


def test_min_size_filters(monkeypatch):
    # Two layers each mention a pair together -> a size-2 cluster exists,
    # but min_size=3 should drop it.
    base = 1_700_000_000
    insights = []
    e = base
    for layer in ("tnet", "ml"):
        for sym in ("AAA", "BBB"):
            insights.append(_insight(e, layer, sym))
            e += 60
    _patch(monkeypatch, insights)
    out = mesh_cluster.clusters(window=400, min_size=3)
    assert out["clusters"] == [], out["clusters"]


def test_related_unknown_symbol(monkeypatch):
    _patch(monkeypatch, _tech_mesh())
    assert mesh_cluster.related("NOPE") == []
    assert mesh_cluster.related("") == []


def test_fail_soft_on_bad_recent(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("mesh down")

    monkeypatch.setattr(mesh, "recent", boom)
    out = mesh_cluster.clusters()
    assert out["clusters"] == []
    assert "generated" in out
    assert mesh_cluster.related("AAPL") == []
