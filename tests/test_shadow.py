"""Tests for the Shadow Lab counterfactual bake-off (hermetic, no network)."""
import datetime
import types
from trader import shadow


def test_blend_and_position():
    s = {"ta": 0.8, "ml": -0.4, "quant": None}
    assert shadow.blend(s, {"ta": 1.0}) == 0.8                  # single-voice
    assert abs(shadow.blend(s, {k: 1.0 for k in shadow._VOICES}) - 0.2) < 1e-9  # equal over present
    assert shadow.blend({}, {"ta": 1.0}) == 0.0
    assert shadow.blend(s, None) == 0.0                         # live handled elsewhere
    assert shadow._position(0.2) == 1 and shadow._position(-0.2) == -1 and shadow._position(0.05) == 0


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB", str(tmp_path / "shadow.db"))
    monkeypatch.setattr(shadow, "_DATA", str(tmp_path))
    import trader.alpha as A
    import trader.crsp.query as Q
    monkeypatch.setattr(A, "analyze", lambda closes, symbol=None, **k:
                        types.SimpleNamespace(composite=0.5, scores={"ta": 0.9, "ml": 0.5, "tnet": 0.3}))

    def fake_bars(sym, start="2024-01-01", end=None, **k):
        base = datetime.date.today() - datetime.timedelta(days=35)
        return [{"date": (base + datetime.timedelta(days=i)).isoformat(), "close": 100 + i}
                for i in range(60)]                              # rising series -> +fwd return
    monkeypatch.setattr(Q, "get_prices", fake_bars)


def test_snapshot_resolve_standings(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    snap = shadow.snapshot(universe=["SPY", "AAA"])
    assert snap["written"] > 0
    # idempotent: a second snapshot the same day writes nothing new
    assert shadow.snapshot(universe=["SPY", "AAA"])["written"] == 0
    res = shadow.resolve()
    assert res["resolved"] > 0
    st = shadow.standings()
    live = next(b for b in st["books"] if b["book"] == "live")
    assert live["trades"] == 2 and live["hit_rate"] == 1.0      # both rose, long positions
    assert live["total_return_pct"] > 0
    assert any(b["book"] == "spy_hold" and b["trades"] == 1 for b in st["books"])
    assert st["leader"] is not None


def test_status_shape(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    s = shadow.status()
    assert "standings" in s and set(["live", "spy_hold", "equal"]).issubset(set(s["books"]))
