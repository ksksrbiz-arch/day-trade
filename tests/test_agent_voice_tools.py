"""Tests for the Performance Auditor's guarded voice-control tools."""
from trader import voices
from trader.agents import tools


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, "STORE", str(tmp_path / "ov.json"))
    monkeypatch.setattr(voices, "_DATA", str(tmp_path))
    voices._cache["val"] = None
    voices._cache["ts"] = 0.0


def _patch_attr(monkeypatch, voice, verdict, attr=0.0):
    import trader.attribution as A
    monkeypatch.setattr(A, "report", lambda *a, **k: {
        "voices": [{"voice": voice, "verdict": verdict, "attributed_return_pct": attr,
                    "lead_decisions": 5}]})


def test_mute_refused_while_maturing(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _patch_attr(monkeypatch, "ta", "maturing (5/20)")
    r = tools.t_mute_voice(voice="ta", on=True)
    assert r.get("refused") is True
    assert "ta" not in voices.overrides()["muted"]


def test_mute_allowed_when_unprofitable(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _patch_attr(monkeypatch, "council", "unprofitable", attr=-0.8)
    r = tools.t_mute_voice(voice="council", on=True)
    assert r.get("muted") is True
    assert "council" in voices.overrides()["muted"]


def test_unmute_always_allowed(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    voices.set_mute("ta", True)
    _patch_attr(monkeypatch, "ta", "maturing (0/20)")        # even with no evidence
    r = tools.t_mute_voice(voice="ta", on=False)
    assert r.get("muted") is False
    assert "ta" not in voices.overrides()["muted"]


def test_pin_requires_profitable(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _patch_attr(monkeypatch, "tnet", "maturing (3/20)")
    r = tools.t_pin_voice(voice="tnet", weight=0.3)
    assert r.get("refused") is True
    _patch_attr(monkeypatch, "tnet", "profitable", attr=1.2)
    r2 = tools.t_pin_voice(voice="tnet", weight=0.3)
    assert r2.get("pinned") == 0.3
    assert voices.overrides()["pinned"].get("tnet") == 0.3


def test_unknown_voice_rejected(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert "error" in tools.t_mute_voice(voice="nope", on=True)


def test_call_name_arg_no_collision(monkeypatch):
    # a tool arg called `name` must not collide with tools.call's signature
    monkeypatch.setitem(tools.REGISTRY, "tmp_t",
                        {"fn": lambda name=None, **k: {"got": name}, "mutating": False, "needs_approval": False})
    assert tools.call("tmp_t", name="MIN_CONFIDENCE") == {"got": "MIN_CONFIDENCE"}
