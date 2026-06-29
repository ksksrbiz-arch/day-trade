"""Mesh theme clustering (hermetic).

Monkeypatch trader.mesh.recent so no DB or live mesh is touched; feed crafted
insights that repeatedly share a distinctive word across layers plus stopword
filler, and assert the clustering surfaces the right themes.
"""
from trader import mesh, mesh_themes


def _fake_insights():
    return [
        {"id": "1", "ts": "t", "day": "d", "layer": "news", "kind": "digest",
         "symbol": "NVDA", "salience": 0.9,
         "text": "Nvidia earnings crush expectations, nvidia guidance strong"},
        {"id": "2", "ts": "t", "day": "d", "layer": "brain", "kind": "regime",
         "symbol": "NVDA", "salience": 0.8,
         "text": "Nvidia leads the risk-on rotation into chips"},
        {"id": "3", "ts": "t", "day": "d", "layer": "tnet", "kind": "forecast",
         "symbol": "NVDA", "salience": 0.7,
         "text": "Forecast: nvidia continues higher over the next session"},
        {"id": "4", "ts": "t", "day": "d", "layer": "reasoning", "kind": "summary",
         "symbol": "", "salience": 0.2,
         "text": "the and for with that this from are was has had but you your"},
    ]


def test_distinctive_term_is_top_theme(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    out = mesh_themes.themes(window=50, top=8)
    assert "themes" in out and "generated" in out
    terms = [t["term"] for t in out["themes"]]
    assert "nvidia" in terms
    # distinctive, repeated, high-salience term should rank near the very top
    assert terms.index("nvidia") <= 1
    nv = next(t for t in out["themes"] if t["term"] == "nvidia")
    assert nv["count"] >= 3
    assert nv["weight"] > 0
    assert "news" in nv["layers"] and "brain" in nv["layers"]


def test_stopwords_excluded(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    out = mesh_themes.themes()
    terms = [t["term"] for t in out["themes"]]
    for sw in ("the", "and", "with", "that", "from"):
        assert sw not in terms


def test_top_terms_shape(monkeypatch):
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: _fake_insights())
    tt = mesh_themes.top_terms(3)
    assert isinstance(tt, list)
    assert len(tt) <= 3
    assert all(isinstance(x, str) for x in tt)


def test_failsoft_on_broken_mesh(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("mesh down")
    monkeypatch.setattr(mesh, "recent", boom)
    out = mesh_themes.themes()
    assert out["themes"] == []
    assert mesh_themes.top_terms(5) == []
