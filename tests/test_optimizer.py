"""Tests for the daily optimizer's pure logic: normalization + evaluate()."""
from dashboard.optimizer import per_1k, clamp, evaluate, CONTROL


def test_per_1k_normalization():
    assert per_1k(-50, 10000) == -5.0     # -$50 on $10k deployed = -5/$1k
    assert per_1k(20, 2000) == 10.0
    assert per_1k(100, 0) == 0.0          # no deployment -> 0


def test_clamp():
    assert clamp(200, 30, 180) == 180
    assert clamp(10, 30, 180) == 30
    assert clamp(90, 30, 180) == 90


def _stat(name, orders=0, deployed=0, realized=0.0, per1k=0.0, avg_conf=0.6,
          cd=30, mc=0.45, failed=0):
    return {"name": name, "orders": orders, "deployed": deployed, "realized": realized,
            "per1k": per1k, "avg_conf": avg_conf, "cooldown_min": cd,
            "min_confidence": mc, "failed": failed, "status": "running"}


def test_overtrading_raises_cooldown():
    stats = {"b1": _stat("churner", orders=30, deployed=30000, realized=-150, per1k=-5.0, cd=30)}
    out = evaluate(stats, {})
    chg = [c for c in out["changes"] if c["param"] == "cooldown_min"]
    assert chg and chg[0]["new"] == 60   # +30 step
    assert any(f["sev"] == "high" for f in out["flags"])


def test_low_conviction_loss_raises_confidence():
    stats = {"b1": _stat("weak", orders=5, deployed=5000, realized=-30, per1k=-6.0,
                          avg_conf=0.48, mc=0.45)}
    out = evaluate(stats, {})
    chg = [c for c in out["changes"] if c["param"] == "min_confidence"]
    assert chg and chg[0]["new"] == 0.50


def test_persistent_bleeder_paused():
    stats = {"b1": _stat("bleeder", orders=4, deployed=4000, realized=-40, per1k=-10.0)}
    history = {"b1": [-9.0, -12.0]}   # prior days also below floor
    out = evaluate(stats, history)
    assert any(c["param"] == "enabled" and c["new"] is False for c in out["changes"])


def test_control_bot_never_tuned():
    stats = {"b1": _stat(CONTROL, orders=40, deployed=40000, realized=-300, per1k=-7.5)}
    out = evaluate(stats, {})
    assert out["changes"] == []          # control is flagged but never auto-tuned
    assert any(f["bot"] == CONTROL for f in out["flags"])


def test_healthy_bot_no_changes_gets_rec():
    stats = {"b1": _stat("winner", orders=4, deployed=4000, realized=40, per1k=10.0)}
    out = evaluate(stats, {})
    assert out["changes"] == []
    assert any("performing" in r["msg"] for r in out["recs"])


def test_zscore_anomaly_flag():
    stats = {"b1": _stat("drift", orders=3, deployed=3000, realized=-9, per1k=-3.0)}
    history = {"b1": [5.0, 6.0, 5.5, 4.8]}   # was positive; now -3 is a downward anomaly
    out = evaluate(stats, history)
    assert any("anomaly" in f["msg"] for f in out["flags"])
