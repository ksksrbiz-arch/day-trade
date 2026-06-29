"""Tests for the unified activity timeline (merge/sort/tone; hermetic)."""
import time
from trader import timeline as tl


def test_to_epoch_formats():
    now = time.time()
    assert abs(tl._to_epoch(now) - now) < 1
    iso = tl._to_epoch("2026-06-26T14:30:00")
    assert iso > 0
    assert tl._to_epoch("garbage") > 0          # fallback to now, never crashes


def test_norm_shape_and_tone_clamp():
    e = tl._norm(time.time(), "news", "x" * 300, "src", "aapl", tone=5)
    assert e["kind"] == "news" and e["symbol"] == "AAPL"
    assert len(e["text"]) <= 200 and e["tone"] == 1   # clamped


def test_events_merge_sorts_desc_and_includes_extra(monkeypatch):
    # neutralize live sources so the test is hermetic & deterministic
    import trader.mesh as mesh, trader.newshub as nh, trader.autonomy as au
    from trader.agents import state
    monkeypatch.setattr(mesh, "recent", lambda *a, **k: [
        {"ts": "2026-06-26T10:00:00", "layer": "brain", "text": "regime high_vol"}])
    monkeypatch.setattr(nh, "aggregate", lambda *a, **k: {"items": [
        {"ts": time.time(), "title": "NVDA soars", "source": "yahoo", "symbols": ["NVDA"], "sentiment": 0.5}]})
    monkeypatch.setattr(state, "recent_traces", lambda n=20: [])
    monkeypatch.setattr(au, "recent_audit", lambda n=12: [])
    extra = [{"ts": time.time() + 5, "kind": "trade", "text": "BUY AAPL", "symbol": "AAPL", "tone": 1}]
    evs = tl.events(limit=10, extra=extra)
    assert evs[0]["kind"] == "trade"                  # newest first (extra ts is latest)
    assert any(e["kind"] == "news" and e["tone"] == 1 for e in evs)
    assert any(e["kind"] == "mesh" for e in evs)
    ts = [e["ts"] for e in evs]
    assert ts == sorted(ts, reverse=True)             # strictly time-sorted desc
