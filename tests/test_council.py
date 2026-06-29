"""Tests for the council's deterministic aggregation + actionability (pure)."""
from trader.council import _parse, aggregate, decide


def v(src, stance, conf=0.7):
    return {"source": src, "stance": stance, "confidence": conf, "rationale": ""}


def test_parse_structured():
    p = _parse("STANCE: BULLISH\nCONFIDENCE: 0.8\nREASON: strong demand")
    assert p["stance"] == "bullish" and p["confidence"] == 0.8 and "demand" in p["rationale"]


def test_parse_defaults_confidence():
    p = _parse("STANCE: BEARISH\nREASON: weak guidance")
    assert p["stance"] == "bearish" and p["confidence"] == 0.5


def test_aggregate_bullish_consensus():
    a = aggregate([v("a", "bullish"), v("b", "bullish"), v("c", "neutral")])
    assert a["consensus"] == "bullish" and a["bull"] == 2 and a["n"] == 3
    assert 0 < a["agreement"] <= 1


def test_aggregate_bearish_consensus():
    a = aggregate([v("a", "bearish", 0.9), v("b", "bearish", 0.8), v("c", "bullish", 0.4)])
    assert a["consensus"] == "bearish" and "c" in a["dissent"]


def test_aggregate_neutral_when_split():
    a = aggregate([v("a", "bullish", 0.7), v("b", "bearish", 0.7)])
    assert a["consensus"] == "neutral"


def test_aggregate_empty():
    a = aggregate([{"source": "x", "stance": None, "confidence": 0}])
    assert a["n"] == 0 and a["consensus"] == "neutral"


def test_decide_proceed():
    a = aggregate([v("a", "bullish"), v("b", "bullish"), v("c", "bullish")])
    d = decide("buy", a)
    assert d["action"] == "proceed"


def test_decide_veto_on_opposition():
    a = aggregate([v("a", "bearish"), v("b", "bearish"), v("c", "bearish")])
    d = decide("buy", a)
    assert d["action"] == "veto"


def test_decide_caution_when_mixed():
    a = aggregate([v("a", "bullish", 0.6), v("b", "bearish", 0.6), v("c", "neutral", 0.5)])
    d = decide("buy", a)
    assert d["action"] in ("caution", "no_signal")


def test_decide_no_signal_empty():
    d = decide("buy", aggregate([]))
    assert d["action"] == "no_signal"
