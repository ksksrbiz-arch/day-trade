"""Tests for the autonomous agent layer: governor bounds, tool registry,
action parsing. LLM/network calls are not exercised here."""
import importlib

from trader.agents import governor, tools
from trader.agents import orchestrator as orch


def test_governor_clamps_and_denies(tmp_path, monkeypatch):
    monkeypatch.setattr(governor, "OVERRIDES", str(tmp_path / "ov.json"))
    monkeypatch.setattr(governor, "ACTIVITY", str(tmp_path / "act.jsonl"))
    # above bound -> clamped to max
    r = governor.propose_param("t", "CONFLUENCE_MIN_SCORE", 0.99, "x")
    assert r["to"] == 0.45 and r["bounded"] is True
    # unknown param -> denied
    d = governor.propose_param("t", "NOT_A_PARAM", 1, "x")
    assert d["kind"] == "denied"
    ov = governor.load_overrides()
    assert ov["CONFLUENCE_MIN_SCORE"] == 0.45


def test_governor_int_param(tmp_path, monkeypatch):
    monkeypatch.setattr(governor, "OVERRIDES", str(tmp_path / "ov.json"))
    monkeypatch.setattr(governor, "ACTIVITY", str(tmp_path / "act.jsonl"))
    r = governor.propose_param("t", "CONFLUENCE_MIN_AGREE", 9, "x")
    assert r["to"] == 4 and isinstance(r["to"], int)


def test_tool_registry_has_read_and_mutating():
    assert "brain_state" in tools.REGISTRY and not tools.REGISTRY["brain_state"]["mutating"]
    assert tools.REGISTRY["run_backtest"]["mutating"] is True
    assert callable(tools.REGISTRY["propose_param"]["fn"])


def test_unknown_tool_call_is_safe():
    assert "error" in tools.call("does_not_exist")


def test_parse_action_plain_json():
    a = orch._parse_action('{"thought":"go","tool":"ml_card","args":{}}')
    assert a["tool"] == "ml_card"


def test_parse_action_with_fences_and_prose():
    txt = 'Sure!\n```json\n{"thought":"x","tool":"brain_state","args":{}}\n```\nDone.'
    a = orch._parse_action(txt)
    assert a["tool"] == "brain_state"


def test_parse_action_garbage_returns_empty():
    assert orch._parse_action("no json here") == {}


def test_roster_providers_diverse():
    provs = {a["provider"] for a in orch.ROSTER}
    assert {"cloudflare", "groq", "vercel"} <= provs
