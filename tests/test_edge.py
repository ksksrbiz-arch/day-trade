"""Tests for the forward edge report (pure logic + fail-soft, no network)."""
import json
import time
from trader import edge


def test_verdict_logic():
    assert edge._verdict({"resolved": 5, "hit_rate": 0.9}, 20).startswith("maturing")
    assert edge._verdict({"resolved": 30, "hit_rate": None}, 20).startswith("maturing")
    assert edge._verdict({"resolved": 30, "hit_rate": 0.62, "avg_dir_return_pct": 0.4}, 20) == "EDGE"
    assert edge._verdict({"resolved": 30, "hit_rate": 0.40}, 20) == "negative"
    assert edge._verdict({"resolved": 30, "hit_rate": 0.50}, 20) == "no edge (~coin flip)"
    # positive hit-rate but negative realized return is NOT an edge
    assert edge._verdict({"resolved": 30, "hit_rate": 0.6, "avg_dir_return_pct": -0.2}, 20) != "EDGE"


def test_tnet_source_resolves(tmp_path, monkeypatch):
    from trader import tnet
    flog = tmp_path / "f.jsonl"
    now = time.time()
    old = now - 10 * 86400      # matured
    rows = [
        {"ts": old, "symbol": "AAA", "raw": 0.5, "ref": 100.0},   # up call
        {"ts": old, "symbol": "BBB", "raw": -0.5, "ref": 100.0},  # down call
        {"ts": now, "symbol": "CCC", "raw": 0.5, "ref": 100.0},   # too fresh -> skipped
    ]
    flog.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(tnet, "_FLOG", str(flog))
    # AAA rose (hit), BBB also rose (miss for a down call)
    monkeypatch.setattr(tnet, "_closes", lambda s: [100.0, 110.0])
    out = edge._tnet_source()
    assert out["source"] == "transformer"
    assert out["signals"] == 3 and out["resolved"] == 2   # fresh one excluded
    assert out["hit_rate"] == 0.5                          # 1 hit / 2


def test_transformer_maturation(tmp_path, monkeypatch):
    from trader import tnet
    flog = tmp_path / "f.jsonl"
    now = time.time()
    rows = []
    for i in range(10):
        rows.append({"ts": now - 10 * 86400, "symbol": f"M{i}", "raw": 0.2, "ref": 100})  # matured
    for i in range(15):
        rows.append({"ts": now - 1 * 86400, "symbol": f"F{i}", "raw": 0.2, "ref": 100})   # fresh
    flog.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(tnet, "_FLOG", str(flog))
    m = edge._transformer_maturation(min_resolved=20, min_age_days=5)
    assert m["matured"] == 10 and m["need"] == 20 and m["total"] == 25
    # the 20th call matures at (now-1d)+5d = now+4d
    assert m["days_to_threshold"] is not None and 3.5 <= m["days_to_threshold"] <= 4.5


def test_report_structure_runs():
    rep = edge.report(window_days=30)
    assert "sources" in rep and "summary" in rep and "counts" in rep
    for s in rep["sources"]:
        assert "verdict" in s and "source" in s
    md = edge.format_report(rep)
    assert md.startswith("# Forward Edge Report")
    assert "| Source |" in md
