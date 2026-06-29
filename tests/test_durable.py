"""Tests for the durable, self-repairing agent runtime (no network)."""
import importlib
import os

from trader.agents import state as st
from trader.agents import actions, supervisor, tools


def _fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "DB", str(tmp_path / "state.db"))


def test_run_resume_and_steps(tmp_path, monkeypatch):
    _fresh_state(tmp_path, monkeypatch)
    rid = st.start_run("autonomy", ["a", "b", "c"])
    assert st.resumable_run("autonomy")[0] == rid
    st.mark_step(rid, 0, "done", {"x": 1})
    assert st.resumable_run("autonomy") == (rid, 1)   # cursor advanced
    assert st.step_status(rid, 0) == "done"
    st.finish_run(rid)
    assert st.resumable_run("autonomy") is None        # no longer running


def test_checkpoint_roundtrip(tmp_path, monkeypatch):
    _fresh_state(tmp_path, monkeypatch)
    rid = st.start_run("autonomy", ["a"])
    st.save_checkpoint(rid, {"results": [{"agent": "Q"}], "k": 2})
    assert st.load_checkpoint(rid)["k"] == 2


def test_approvals_lifecycle(tmp_path, monkeypatch):
    _fresh_state(tmp_path, monkeypatch)
    aid = st.create_approval("Quant", "run_python", {"args": {"code": "1+1"}})
    assert len(st.pending_approvals()) == 1
    r = st.resolve_approval(aid, "approved")
    assert r["status"] == "approved"
    assert st.pending_approvals() == []


def test_traces_and_kv(tmp_path, monkeypatch):
    _fresh_state(tmp_path, monkeypatch)
    rid = st.start_run("autonomy", ["a"])
    st.trace(rid, "Quant", "agent", "ml_card", "done", 42, "ok")
    assert st.recent_traces()[0]["agent"] == "Quant"
    st.kv_set("lesson", {"x": 1})
    assert st.kv_get("lesson")["x"] == 1


def test_file_write_sandbox_enforced():
    # absolute escape attempt must be rejected
    r = actions.file_write(path="../../../etc/passwd", content="x")
    assert "error" in r


def test_run_python_executes():
    r = actions.run_python(code="print(6*7)")
    assert r.get("returncode") == 0 and "42" in r.get("stdout", "")


def test_supervisor_services_and_check(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "DB", str(tmp_path / "s.db"))
    assert "dashboard" in supervisor.SERVICES and "agents" in supervisor.SERVICES
    h = supervisor.check_and_heal(heal=False)   # observe only, never launches
    assert set(h["services"]) == set(supervisor.SERVICES)
    assert h["restarted"] == []


def test_sensitive_tools_need_approval():
    assert tools.REGISTRY["run_python"]["needs_approval"] is True
    assert tools.REGISTRY["file_write"]["needs_approval"] is True
    assert tools.REGISTRY["brain_state"]["needs_approval"] is False
