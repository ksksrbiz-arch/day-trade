"""Tests for voice performance attribution (math + decomposition identity)."""
import json
import numpy as np
from trader import attribution, backprop


def _setup(tmp_path, monkeypatch, rows, price_map):
    dec = tmp_path / "decisions.jsonl"
    dec.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(backprop, "DECISIONS", str(dec))
    monkeypatch.setattr(backprop, "WEIGHTS", str(tmp_path / "w.json"))  # untrained -> static base
    # price_map: symbol -> (p0, p1)
    monkeypatch.setattr(backprop, "_price_fn", lambda sym, asset, day, h: price_map.get(sym))


def test_attribution_decomposes_total(tmp_path, monkeypatch):
    rows = [
        {"symbol": "AAA", "day": "2026-01-02", "asset": "equity", "horizon": 5,
         "scores": {"ta": 0.8, "ml": 0.6, "quant": -0.2}},
        {"symbol": "BBB", "day": "2026-01-02", "asset": "equity", "horizon": 5,
         "scores": {"ta": -0.7, "ml": -0.5}},
    ]
    # AAA composite>0 and rose (+10%) -> r_dir>0 ; BBB composite<0 and fell (-5%) -> r_dir>0
    prices = {"AAA": (100.0, 110.0), "BBB": (100.0, 95.0)}
    _setup(tmp_path, monkeypatch, rows, prices)
    rep = attribution.report(min_decisions=1)
    assert rep["resolved"] == 2
    # per-voice attributions must sum to the total directional return (decomposition identity)
    s = sum(v["attributed_return_pct"] for v in rep["voices"])
    assert abs(s - rep["total_dir_return_pct"]) < 1e-6
    # both trades were directionally correct -> total positive
    assert rep["total_dir_return_pct"] > 0


def test_agreeing_voice_credited(tmp_path, monkeypatch):
    # ta strongly bullish and the name rises -> ta should earn positive attribution
    rows = [{"symbol": "AAA", "day": "2026-01-02", "asset": "equity", "horizon": 5,
             "scores": {"ta": 0.9, "ml": 0.1}}]
    _setup(tmp_path, monkeypatch, rows, {"AAA": (100.0, 112.0)})
    rep = attribution.report(min_decisions=1)
    ta = next(v for v in rep["voices"] if v["voice"] == "ta")
    assert ta["attributed_return_pct"] > 0
    assert ta["lead_decisions"] == 1 and ta["lead_hit_rate"] == 1.0


def test_empty_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(backprop, "DECISIONS", str(tmp_path / "none.jsonl"))
    rep = attribution.report()
    assert rep["resolved"] == 0 and rep["voices"] == []
    md = attribution.format_report(rep)
    assert md.startswith("# Voice Attribution")
