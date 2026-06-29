"""Mesh graph analytics (hermetic)."""
from trader import mesh


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh, "DB", str(tmp_path / "mesh.db"))
    monkeypatch.setattr(mesh, "_DATA", str(tmp_path))


def test_graph_nodes_edges(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    mesh.publish("brain", "regime", "risk_on", symbol="SPY", salience=0.8, to_ltm=False)
    mesh.publish("tnet", "forecast", "SPY up", symbol="SPY", salience=0.6, to_ltm=False)
    mesh.publish("ml", "model", "auc 0.6", symbol="", salience=0.3, to_ltm=False)
    g = mesh.graph(100)
    assert g["metrics"]["total"] == 3
    layers = {n["layer"] for n in g["nodes"]}
    assert {"brain", "tnet", "ml"} <= layers
    # brain & tnet co-mentioned SPY -> an edge between them, with attention + flags
    e = next(e for e in g["edges"] if {e["a"], e["b"]} == {"brain", "tnet"})
    assert "attention" in e and "pruned" in e and 0.0 <= e["attention"] <= 1.0
    assert g["metrics"]["salience"]["high"] >= 1
    # GNN node fields present; connected layers carry influence + degree
    bn = next(n for n in g["nodes"] if n["layer"] == "brain")
    assert "influence" in bn and "degree" in bn and bn["degree"] >= 1


def test_graph_pruning_denoises(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    # one strong, recent, high-salience co-mention (AAA) and many weak ones
    mesh.publish("brain", "r", "x", symbol="AAA", salience=0.9, to_ltm=False)
    mesh.publish("tnet", "f", "x", symbol="AAA", salience=0.9, to_ltm=False)
    mesh.publish("ml", "m", "x", symbol="ZZZ", salience=0.05, to_ltm=False)
    mesh.publish("news", "n", "x", symbol="ZZZ", salience=0.05, to_ltm=False)
    g = mesh.graph(100)
    strong = next(e for e in g["edges"] if {e["a"], e["b"]} == {"brain", "tnet"})
    weak = next(e for e in g["edges"] if {e["a"], e["b"]} == {"ml", "news"})
    assert strong["attention"] > weak["attention"]
    assert weak["pruned"] and not strong["pruned"]


def test_temporal_coactivation_edges(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    # two layers fire in the same window with NO shared symbol -> temporal edge
    mesh.publish("brain", "regime", "risk_on", salience=0.7, to_ltm=False)
    mesh.publish("autonomy", "applied", "tuned", salience=0.7, to_ltm=False)
    g = mesh.graph(100)
    e = next((e for e in g["edges"] if {e["a"], e["b"]} == {"brain", "autonomy"}), None)
    assert e is not None and e["kind"] == "temporal"
    assert g["metrics"]["temporal_edges"] >= 1


def test_graph_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    g = mesh.graph(10)
    assert g["metrics"]["total"] == 0 and g["nodes"] == []
