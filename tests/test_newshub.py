"""Tests for the unified news aggregator (pure logic; no network)."""
import time
from trader import newshub as nh


def test_lexicon_sentiment():
    assert nh.lex_sentiment("Stock surges to record high after earnings beat") > 0
    assert nh.lex_sentiment("Shares plunge on fraud probe and downgrade") < 0
    assert nh.lex_sentiment("Company announces annual meeting date") == 0.0


def test_extract_symbols():
    s = nh.extract_symbols("$NVDA and AAPL rally; random WORD", universe={"AAPL", "MSFT"})
    assert "NVDA" in s and "AAPL" in s and "WORD" not in s


def test_dedupe_merges_and_prefers_strong_source():
    a = nh._norm("Big Co beats earnings", "", "", "googlenews", "ticker", time.time(), ["BIG"])
    b = nh._norm("Big Co beats earnings", "", "", "SEC 8-K", "filing", time.time(), ["XYZ"])
    out = nh._dedupe([a, b])
    assert len(out) == 1
    assert out[0]["category"] == "filing"                      # higher source weight wins
    assert set(out[0]["symbols"]) == {"BIG", "XYZ"}            # symbols merged


def test_rank_recency_and_salience():
    now = time.time()
    old = nh._norm("calm update", "", "", "rss", "markets", now - 48 * 3600, [])
    fresh = nh._norm("stock soars on upgrade", "", "", "rss", "markets", now - 60, [])
    ranked = nh._rank([old, fresh])
    assert ranked[0]["title"] == "stock soars on upgrade"


def test_market_sentiment_and_catalysts():
    now = time.time()
    items = [
        nh._norm("SPY rallies to record high", "", "", "rss", "markets", now, ["SPY"]),
        nh._norm("NVDA plunges on downgrade", "", "", "y", "ticker", now, ["NVDA"]),
        nh._norm("NVDA soars after beat", "", "", "g", "ticker", now, ["NVDA"]),
        nh._norm("WSB buzz", "", "", "wsb", "social", now, ["NVDA"]),
    ]
    ms = nh.market_sentiment(items)
    assert ms["n"] == 3 and ms["label"] in ("risk-on", "risk-off", "mixed")
    cat = nh.catalysts("NVDA", items)
    assert all("NVDA" in c["symbols"] for c in cat) and len(cat) >= 2
