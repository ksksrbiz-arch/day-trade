"""Tests for the prediction layer (store, matrix, hypothesis parsing) -- no network."""
from trader.predict import store, hypothesis


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "p.db"))


def test_record_idempotent(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = store.record_prediction("wsb", "WEN", "up", 5, 10, "fries", 0.7, "risk_on", 20.0)
    b = store.record_prediction("wsb", "WEN", "up", 5, 10, "fries", 0.7, "risk_on", 20.0)
    assert a[1] is True and b[1] is False        # second is deduped
    assert store.stats()["watching"] == 1


def test_bucket_key_stable():
    k1 = store.bucket_key("wsb", "up", 5, 10, "risk_on")
    k2 = store.bucket_key("wsb", "up", 6, 12, "risk_on")   # same buckets
    assert k1 == k2 and "d4-7" in k1 and "m5-15" in k1


def test_matrix_cold_start_is_neutral(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    ms = store.matrix_score(store.bucket_key("wsb", "up", 5, 10, "risk_on"))
    assert ms["prob"] == 0.5 and ms["confidence"] < 0.4


def test_resolve_and_matrix_build(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    # two up-predictions; price_fn says one rose 10%, one fell 10%
    store.record_prediction("wsb", "AAA", "up", 5, 9, "x", 0.6, "risk_on", 100.0)
    store.record_prediction("wsb", "BBB", "up", 5, 9, "y", 0.6, "risk_on", 100.0)

    def price_fn(sym, asset, day, hz):
        return (100.0, 110.0) if sym == "AAA" else (100.0, 90.0)

    store.resolve_due(price_fn)
    st = store.stats()
    assert st["correct"] == 1 and st["incorrect"] == 1
    # matrix now has the bucket with hit_rate 0.5 over n=2
    mx = {b["key"]: b for b in store.decision_matrix(min_n=1)}
    key = store.bucket_key("wsb", "up", 5, 9, "risk_on")
    assert mx[key]["n"] == 2 and abs(mx[key]["hit_rate"] - 0.5) < 1e-9


def test_hypothesis_parse_json():
    obj = hypothesis._parse_json('```json\n{"hypotheses":[{"symbol":"WEN","direction":"up"}]}\n```')
    assert obj["hypotheses"][0]["symbol"] == "WEN"


def test_hypothesis_crypto_mapping(monkeypatch):
    # offline: feed extract via monkeypatched cloudflare chat
    import trader.agents.cloudflare as cf
    monkeypatch.setattr(cf, "available", lambda: True)
    monkeypatch.setattr(cf, "chat", lambda *a, **k:
                        '{"hypotheses":[{"symbol":"BTC","direction":"up","magnitude_pct":20,'
                        '"horizon_days":10,"confidence":0.6,"rationale":"breakout"}]}')
    out = hypothesis.extract(["BTC breakout incoming"])
    assert out and out[0]["symbol"] == "BTC/USD" and out[0]["asset"] == "crypto"
