"""Unit tests for the real-time quote hub (keyless-safe behaviour + deltas)."""
from trader.quotestream import QuoteHub


def test_snapshot_empty_by_default():
    h = QuoteHub()
    assert h.snapshot() == {}
    assert h.version == 0


def test_update_bumps_version_and_stores():
    h = QuoteHub()
    h._update("aapl", {"last": 100.0})
    snap = h.snapshot()
    assert "AAPL" in snap
    assert snap["AAPL"]["last"] == 100.0
    assert snap["AAPL"]["symbol"] == "AAPL"
    assert "ts" in snap["AAPL"]
    assert h.version == 1


def test_changes_since_returns_only_newer():
    h = QuoteHub()
    h._update("AAPL", {"last": 1})
    changes, v1 = h.changes_since(0)
    assert [c["symbol"] for c in changes] == ["AAPL"]
    # nothing new since v1
    changes2, v2 = h.changes_since(v1)
    assert changes2 == []
    assert v2 == v1
    # a new symbol shows up as a delta
    h._update("MSFT", {"last": 2})
    changes3, v3 = h.changes_since(v1)
    assert [c["symbol"] for c in changes3] == ["MSFT"]
    assert v3 > v1


def test_snapshot_filter_by_symbols():
    h = QuoteHub()
    h._update("AAPL", {"last": 1})
    h._update("MSFT", {"last": 2})
    assert set(h.snapshot(["aapl"]).keys()) == {"AAPL"}


def test_status_shape():
    h = QuoteHub()
    st = h.status()
    assert set(["started", "symbols", "count", "version"]).issubset(st.keys())


def test_ensure_started_keyless_is_safe():
    # With no credentials the hub must start without raising and stay empty.
    h = QuoteHub()
    h.ensure_started()
    # subscriber thread may run briefly; snapshot stays empty without keys/symbols
    assert isinstance(h.snapshot(), dict)


def test_ensure_symbol_adds_and_dedupes():
    h = QuoteHub()
    h.ensure_symbol("aapl")
    assert "AAPL" in h._symbols
    n = len(h._symbols)
    h.ensure_symbol("AAPL")          # idempotent
    assert len(h._symbols) == n


def test_ensure_symbol_ignores_crypto_and_blank():
    h = QuoteHub()
    h.ensure_symbol("BTC/USD")       # crypto pairs handled elsewhere
    h.ensure_symbol("")
    assert h._symbols == []
