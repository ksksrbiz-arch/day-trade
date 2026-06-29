"""Tests for the bidirectional learning loop: reliability-weighted council
aggregation + agent-reliability weighting math."""
from trader import council
from trader.ml import agent_reliability as ar


def _votes():
    return [
        {"source": "groq", "stance": "bullish", "confidence": 0.6, "rationale": ""},
        {"source": "cohere", "stance": "bearish", "confidence": 0.6, "rationale": ""},
    ]


def test_aggregate_unweighted_is_balanced():
    agg = council.aggregate(_votes())
    assert abs(agg["score"]) < 0.01            # equal & opposite -> ~neutral


def test_aggregate_weights_shift_consensus():
    # trust groq 1.8x, distrust cohere 0.4x -> consensus should tilt bullish
    w = {"groq": 1.8, "cohere": 0.4}
    agg = council.aggregate(_votes(), w)
    assert agg["score"] > 0.2 and agg["consensus"] == "bullish"


def test_aggregate_backward_compatible():
    # passing no weights must equal passing all-1.0 weights
    a = council.aggregate(_votes())
    b = council.aggregate(_votes(), {"groq": 1.0, "cohere": 1.0})
    assert a["score"] == b["score"]


def test_reliability_weights_default_neutral_when_thin(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "REL", str(tmp_path / "rel.json"))
    # no file -> empty -> weights() returns {}
    assert ar.weights() == {}


def test_reliability_weight_mapping(tmp_path, monkeypatch):
    import json
    relf = tmp_path / "rel.json"
    json.dump({"groq": {"right": 8, "total": 10, "acc": 0.8},
               "cohere": {"right": 3, "total": 10, "acc": 0.3},
               "new": {"right": 1, "total": 2, "acc": 0.5}}, open(relf, "w"))
    monkeypatch.setattr(ar, "REL", str(relf))
    w = ar.weights(min_n=5)
    assert w["groq"] > 1.0          # accurate agent gains influence
    assert w["cohere"] < 1.0        # inaccurate agent loses influence
    assert w["new"] == 1.0          # thin history -> neutral


def test_outcomes_importable():
    from trader.ml.outcomes import trade_samples
    out = trade_samples(horizon=10)
    assert isinstance(out, tuple) and len(out) == 4
