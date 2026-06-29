"""Cortex telemetry: live history logging + calibration (hermetic)."""
from trader import cortex


def test_log_live_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex, "_DATA", str(tmp_path))
    monkeypatch.setattr(cortex, "HIST", str(tmp_path / "history.jsonl"))
    cortex.log_live({"ta": 0.5}, {"p_up": 0.62, "conviction": 0.24, "confidence": 0.8})
    cortex.log_live({"ta": 0.5}, {"p_up": 0.62, "conviction": 0.24, "confidence": 0.8})  # dup -> skipped
    cortex.log_live({"ta": 0.5}, {"p_up": 0.40, "conviction": -0.20, "confidence": 0.7})
    h = cortex.history(10)
    assert len(h) == 2                     # dup collapsed
    assert h[-1]["p_up"] == 0.40


def test_calibration_untrained(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex, "_cache", {"ts": 0.0, "params": None, "loaded": False})
    monkeypatch.setattr(cortex, "WEIGHTS", str(tmp_path / "none.npz"))
    assert cortex.calibration() == {"trained": False}
