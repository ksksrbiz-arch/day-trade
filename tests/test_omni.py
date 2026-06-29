"""Tests for the Omni stance parser + opposition logic (pure, no network)."""
from trader.omni import parse_stance, opposes


def test_parse_explicit_leading_word():
    assert parse_stance("BULLISH. Strong demand into earnings.") == "bullish"
    assert parse_stance("Bearish — guidance looks weak.") == "bearish"
    assert parse_stance("NEUTRAL, mixed signals.") == "neutral"


def test_parse_by_tally():
    assert parse_stance("Upside looks strong, positive momentum and an upgrade.") == "bullish"
    assert parse_stance("Downside risk, weak guidance, a downgrade and caution.") == "bearish"


def test_parse_empty_is_neutral():
    assert parse_stance("") == "neutral"
    assert parse_stance("It could go either way.") == "neutral"


def test_opposes():
    assert opposes("buy", "bearish") is True
    assert opposes("sell", "bullish") is True
    assert opposes("buy", "bullish") is False
    assert opposes("buy", "neutral") is False
    assert opposes("sell", "bearish") is False
