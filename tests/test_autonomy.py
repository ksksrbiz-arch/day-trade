"""Tests for the platform-wide autonomy controller (policy, guards, safety)."""
from trader import autonomy


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomy, "POLICY", str(tmp_path / "policy.json"))
    monkeypatch.setattr(autonomy, "AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(autonomy, "_DATA", str(tmp_path))
    # neutralize evidence sources so non-target actions block fast & hermetic
    monkeypatch.setattr(autonomy, "_attr_voices", lambda: [])
    monkeypatch.setattr(autonomy, "_edge_source", lambda n: None)
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 1.0)      # fresh -> maintenance idle
    monkeypatch.setattr(autonomy, "_lines", lambda p: 0)        # small logs -> no prune/recal


def test_policy_default_and_set(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert autonomy.policy()["mode"] == "propose"
    autonomy.set_policy(mode="auto")
    assert autonomy.policy()["mode"] == "auto"
    autonomy.set_policy(mode="bogus")          # invalid -> ignored
    assert autonomy.policy()["mode"] == "auto"
    autonomy.set_policy(kill_switch=True)
    assert autonomy.policy()["kill_switch"] is True


def test_kill_switch_and_off_block_sweep(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    autonomy.set_policy(mode="auto", kill_switch=True)
    assert autonomy.sweep()["disabled"] is True
    autonomy.set_policy(mode="off", kill_switch=False)
    assert autonomy.sweep()["disabled"] is True


def test_risk_guard_guard_logic(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import trader.market_brain as mb
    from trader.agents import governor
    monkeypatch.setattr(governor, "load_overrides", lambda: {})       # MIN_CONFIDENCE default
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "high_vol")
    ev = autonomy._ev_risk_guard()
    assert ev["eligible"] and ev["proposal"]["name"] == "MIN_CONFIDENCE" and ev["proposal"]["value"] == 0.65
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "neutral")
    assert autonomy._ev_risk_guard()["eligible"] is False


def test_propose_mode_does_not_mutate(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    autonomy.set_policy(mode="propose")
    import trader.market_brain as mb
    from trader.agents import governor
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "high_vol")
    monkeypatch.setattr(governor, "load_overrides", lambda: {})
    applied = []
    monkeypatch.setattr(governor, "propose_param", lambda *a, **k: applied.append(a) or {})
    r = autonomy.sweep()
    statuses = {x["action"]: x["status"] for x in r["results"]}
    assert statuses["risk_guard"] == "proposed"      # proposed, not applied
    assert applied == []                              # nothing mutated


def test_self_maintenance_actions(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(autonomy, "_lines", lambda p: 5000)
    assert autonomy._ev_prune_data_logs()["eligible"] is True
    monkeypatch.setattr(autonomy, "_lines", lambda p: 100)
    assert autonomy._ev_prune_data_logs()["eligible"] is False
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 30.0)
    assert autonomy._ev_retrain_stale_ml()["eligible"] is True       # stale -> retrain
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 1.0)
    assert autonomy._ev_retrain_stale_ml()["eligible"] is False      # fresh
    monkeypatch.setattr(autonomy, "_age_h", lambda p: None)
    assert autonomy._ev_retrain_stale_ml()["eligible"] is True       # no model yet


def test_recalibrate_and_relax(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(autonomy, "_lines", lambda p: 50)
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 48.0)
    assert autonomy._ev_recalibrate_tnet()["eligible"] is True
    monkeypatch.setattr(autonomy, "_lines", lambda p: 10)
    assert autonomy._ev_recalibrate_tnet()["eligible"] is False      # not enough forecasts
    monkeypatch.setattr(autonomy, "_edge_source", lambda n: {"resolved": 30, "hit_rate": 0.7})
    monkeypatch.setattr(autonomy, "_cur_param", lambda n, d: 0.30)
    ev = autonomy._ev_relax_selectivity()
    assert ev["eligible"] and ev["proposal"]["value"] == 0.27
    monkeypatch.setattr(autonomy, "_edge_source", lambda n: {"resolved": 30, "hit_rate": 0.5})
    assert autonomy._ev_relax_selectivity()["eligible"] is False     # not accurate enough to loosen


def test_train_cortex_guard(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from trader import cortex
    monkeypatch.setattr(autonomy, "_cortex_samples", lambda: 50)
    # untrained + no card yet (age None) + enough samples -> eligible
    monkeypatch.setattr(cortex, "card", lambda: {"trained": False})
    monkeypatch.setattr(autonomy, "_age_h", lambda p: None)
    assert autonomy._ev_train_cortex()["eligible"] is True
    # untrained but recently attempted (<6h) -> blocked
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 2.0)
    assert autonomy._ev_train_cortex()["eligible"] is False
    # trained + fresh -> blocked
    monkeypatch.setattr(cortex, "card", lambda: {"trained": True})
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 10.0)
    assert autonomy._ev_train_cortex()["eligible"] is False
    # untrained + old + too few samples -> blocked
    monkeypatch.setattr(cortex, "card", lambda: {"trained": False})
    monkeypatch.setattr(autonomy, "_age_h", lambda p: 12.0)
    monkeypatch.setattr(autonomy, "_cortex_samples", lambda: 5)
    assert autonomy._ev_train_cortex()["eligible"] is False


def test_auto_mode_applies_autosafe(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    autonomy.set_policy(mode="auto")
    import trader.market_brain as mb
    from trader.agents import governor
    monkeypatch.setattr(mb, "cached_regime", lambda *a, **k: "high_vol")
    monkeypatch.setattr(governor, "load_overrides", lambda: {})
    applied = []
    monkeypatch.setattr(governor, "propose_param", lambda *a, **k: applied.append(a) or {"ok": True})
    r = autonomy.sweep()
    statuses = {x["action"]: x["status"] for x in r["results"]}
    assert statuses["risk_guard"] == "applied"        # auto-safe + eligible -> applied
    assert any(c[1] == "MIN_CONFIDENCE" for c in applied)
