"""Wave 5: tnet.scan ranking + reasoning conviction distribution (hermetic)."""
import numpy as np
from trader import tnet, reasoning


def test_tnet_scan(monkeypatch):
    rng = np.random.default_rng(0)
    series = list(np.cumsum(rng.normal(0.1, 1.0, size=300)) + 100)   # trending up
    monkeypatch.setattr(tnet, "_closes", lambda s: series)
    monkeypatch.setattr(tnet, "forecast_ensemble", lambda s: {"stability": 0.8})
    out = tnet.scan(["AAA", "BBB"])
    assert out["n"] == 2
    r = out["ranked"][0]
    assert {"symbol", "direction", "raw", "prob_up", "confidence", "stability", "strength"} <= set(r)
    # sorted by strength descending
    assert out["ranked"][0]["strength"] >= out["ranked"][-1]["strength"]


def test_tnet_scan_skips_short(monkeypatch):
    monkeypatch.setattr(tnet, "_closes", lambda s: [1, 2, 3])     # too short
    assert tnet.scan(["X"])["n"] == 0


class _Conv:
    def __init__(self, composite, side, gate=True):
        self.composite = composite; self.side = side; self.agree = 2
        self.n_methods = 3; self.gate_pass = gate; self.size_mult = 1.0
        self.scores = {"ta": composite}; self.weights = {"ta": 1.0}; self.reason = "t"


def test_reasoning_conviction_hist(tmp_path, monkeypatch):
    monkeypatch.setattr(reasoning, "DB", str(tmp_path / "r.db"))
    monkeypatch.setattr(reasoning, "_DATA", str(tmp_path))
    import trader.mesh as _mesh
    monkeypatch.setattr(_mesh, "publish", lambda *a, **k: True)
    reasoning.record("AAA", _Conv(0.05, "buy"))
    reasoning.record("BBB", _Conv(-0.25, "sell"))
    reasoning.record("CCC", _Conv(0.62, "buy"))
    st = reasoning.stats()
    assert st["total"] == 3 and st["buys"] == 2 and st["sells"] == 1
    hist = {b["bucket"]: b["n"] for b in st["conviction_hist"]}
    assert hist["0-.1"] == 1 and hist[".2-.3"] == 1 and hist[".5+"] == 1
