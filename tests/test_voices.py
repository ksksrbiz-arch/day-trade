"""Tests for voice overrides (mute/pin) and confluence honoring them."""
from trader import voices, alpha


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, "STORE", str(tmp_path / "ov.json"))
    monkeypatch.setattr(voices, "_DATA", str(tmp_path))
    voices._cache["val"] = None
    voices._cache["ts"] = 0.0


def test_store_mute_and_pin_roundtrip(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    voices.set_mute("ta", True)
    voices.set_pin("ml", 0.4)
    ov = voices.overrides()
    assert "ta" in ov["muted"] and ov["pinned"]["ml"] == 0.4
    voices.set_mute("ta", False)
    voices.set_pin("ml", None)
    ov = voices.overrides()
    assert "ta" not in ov["muted"] and "ml" not in ov["pinned"]


def test_confluence_drops_muted_voice(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    voices.set_mute("ta", True)
    c = alpha.confluence(ta=0.9, ml=0.2, regime="neutral")
    assert "ta" not in c.weights and "ta" not in c.scores   # muted -> not voting
    assert "ml" in c.weights


def test_confluence_honors_pin_weight(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    voices.set_pin("tnet", 0.95)
    c = alpha.confluence(tnet=0.5, ml=0.5, regime="neutral")
    assert c.weights["tnet"] > c.weights["ml"]              # pinned weight dominates


def test_summary_shape(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    s = voices.summary("neutral")
    assert "voices" in s and len(s["voices"]) == len(voices.METHODS)
    for v in s["voices"]:
        assert {"voice", "base", "effective", "muted", "pinned"} <= set(v)
