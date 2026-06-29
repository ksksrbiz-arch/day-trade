"""
Eval suite built from REAL failure modes (the trading analogues of duplicate
payment / bad vendor / hallucinated SKU / over-budget / wrong refund).
Each case asserts the policy engine catches it. This is the closed-loop safety net.
"""
from trader.policy import evaluate, PolicyConfig
from trader.resilience import call, confidence, backoff_delay

POL = PolicyConfig(max_notional=5000, max_concurrent=25, max_daily_trades=40)
BASE_CTX = {"open_symbols": set(), "n_positions": 0, "day_trades": 0,
            "confidence": 0.7, "paper": True, "universe": set()}


def _act(sym="AAPL", side="buy", notional=1000, instrument="equity"):
    return {"symbol": sym, "side": side, "notional": notional, "instrument": instrument}


# ---- real failure modes -> must DENY ----

def test_duplicate_position_denied():
    ctx = {**BASE_CTX, "open_symbols": {"AAPL"}}
    assert evaluate(_act("AAPL"), ctx, POL)["decision"] == "deny"


def test_hallucinated_symbol_denied():
    assert evaluate(_act(sym=""), BASE_CTX, POL)["decision"] == "deny"


def test_out_of_universe_denied():
    ctx = {**BASE_CTX, "universe": {"AAPL", "MSFT"}}
    assert evaluate(_act("FAKECO"), ctx, POL)["decision"] == "deny"


def test_halted_symbol_denied():
    ctx = {**BASE_CTX, "halted": {"GME"}}
    assert evaluate(_act("GME"), ctx, POL)["decision"] == "deny"


def test_daily_cap_denied():
    ctx = {**BASE_CTX, "day_trades": 40}
    assert evaluate(_act(), ctx, POL)["decision"] == "deny"


def test_max_concurrent_denied():
    ctx = {**BASE_CTX, "n_positions": 25}
    assert evaluate(_act(), ctx, POL)["decision"] == "deny"


def test_real_money_denied():
    ctx = {**BASE_CTX, "paper": False}
    assert evaluate(_act(), ctx, POL)["decision"] == "deny"


# ---- over-scope / low-reversibility -> must ESCALATE (not auto-execute) ----

def test_oversize_escalates():
    assert evaluate(_act(notional=9000), BASE_CTX, POL)["decision"] == "escalate"


def test_low_conf_short_escalates():
    ctx = {**BASE_CTX, "confidence": 0.3}
    assert evaluate(_act(side="sell"), ctx, POL)["decision"] == "escalate"


def test_low_conf_option_escalates():
    ctx = {**BASE_CTX, "confidence": 0.4}
    assert evaluate(_act(instrument="option"), ctx, POL)["decision"] == "escalate"


# ---- clean trade -> APPROVE ----

def test_clean_trade_approved():
    v = evaluate(_act(), BASE_CTX, POL)
    assert v["decision"] == "approve" and v["reversibility"] == 1.0


def test_option_lower_reversibility():
    assert evaluate(_act(instrument="option"), {**BASE_CTX, "confidence": 0.8}, POL)["reversibility"] == 0.5


# ---- resilience ----

def test_retry_then_success():
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"
    r = call(flaky, kind="network")
    assert r["ok"] and r["value"] == "ok" and r["attempts"] == 2


def test_fallback_route():
    def always_fail():
        raise RuntimeError("down")
    r = call(always_fail, kind="llm", fallback=lambda: "fallback-answer")
    assert r["ok"] and r["fell_back"] and r["value"] == "fallback-answer"


def test_total_failure_returns_not_ok():
    r = call(lambda: (_ for _ in ()).throw(RuntimeError("x")), kind="broker")
    assert r["ok"] is False and r["confidence"] == 0.0


def test_confidence_erodes_with_retries_and_fallback():
    assert confidence(1, False, True) == 1.0
    assert confidence(3, False, True) < 1.0
    assert confidence(1, True, True) < 1.0


def test_backoff_grows():
    assert backoff_delay("llm", 2) >= backoff_delay("llm", 0)
