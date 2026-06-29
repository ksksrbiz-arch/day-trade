"""Reasoning trace store: record, idempotency, leaderboard (hermetic)."""
from trader import reasoning


class _Conv:
    def __init__(self, composite, side, scores, weights, gate=True):
        self.composite = composite; self.side = side; self.agree = 3
        self.n_methods = 4; self.gate_pass = gate; self.size_mult = 1.2
        self.scores = scores; self.weights = weights; self.reason = "test"


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(reasoning, "DB", str(tmp_path / "r.db"))
    monkeypatch.setattr(reasoning, "_DATA", str(tmp_path))
    import trader.mesh as _mesh
    monkeypatch.setattr(_mesh, "publish", lambda *a, **k: True)   # hermetic: no real mesh writes


def test_record_idempotent(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _Conv(0.42, "buy", {"ta": 0.5, "ml": 0.4}, {"ta": 0.6, "ml": 0.4})
    assert reasoning.record("NVDA", c, "risk_on") is True
    assert reasoning.record("NVDA", c, "risk_on") is False     # same day/side/comp
    assert reasoning.stats()["total"] == 1


def test_gated_decision_publishes_to_mesh(tmp_path, monkeypatch):
    monkeypatch.setattr(reasoning, "DB", str(tmp_path / "r.db"))
    monkeypatch.setattr(reasoning, "_DATA", str(tmp_path))
    calls = []
    import trader.mesh as _mesh
    monkeypatch.setattr(_mesh, "publish", lambda *a, **k: calls.append((a, k)) or True)
    reasoning.record("NVDA", _Conv(0.45, "buy", {"ta": 0.6, "ml": 0.5}, {"ta": 0.6, "ml": 0.4}, gate=True))
    assert calls and calls[0][0][0] == "reasoning"     # published under the reasoning layer
    # low-conviction / non-gated should NOT publish
    calls.clear()
    reasoning.record("AAA", _Conv(0.10, "buy", {"ta": 0.1}, {"ta": 1.0}, gate=False))
    assert calls == []


def test_leaderboard_and_recent(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    reasoning.record("AAA", _Conv(0.5, "buy", {"ta": 0.8, "ml": 0.1}, {"ta": 0.7, "ml": 0.3}), "neutral")
    reasoning.record("BBB", _Conv(-0.3, "sell", {"ta": 0.1, "ml": -0.8}, {"ta": 0.4, "ml": 0.6}), "neutral")
    lb = reasoning.voice_leaderboard()
    assert any(v["voice"] == "ta" for v in lb) and any(v["voice"] == "ml" for v in lb)
    assert len(reasoning.recent(10)) == 2
    assert len(reasoning.recent(10, gated_only=True)) == 2
