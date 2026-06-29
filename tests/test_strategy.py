from trader.labels import Label
from trader.strategy import StrategyConfig, decide


def L(tickers, sentiment, confidence, event="earnings"):
    return Label(tickers=tickers, sentiment=sentiment, confidence=confidence, event_type=event)


def test_buys_on_strong_bullish_signal():
    cfg = StrategyConfig(universe={"TSLA"})
    intent = decide(L(["TSLA"], 0.7, 0.8), cfg)
    assert intent is not None
    assert intent.side == "buy"
    assert intent.symbol == "TSLA"


def test_skips_low_confidence():
    cfg = StrategyConfig(universe={"TSLA"})
    assert decide(L(["TSLA"], 0.9, 0.5), cfg) is None  # conf below 0.6


def test_skips_weak_sentiment():
    cfg = StrategyConfig(universe={"TSLA"})
    assert decide(L(["TSLA"], 0.3, 0.9), cfg) is None  # sentiment below 0.4


def test_skips_noise_event():
    cfg = StrategyConfig(universe={"TSLA"})
    assert decide(L(["TSLA"], 0.9, 0.9, event="noise"), cfg) is None


def test_no_short_unless_enabled():
    cfg = StrategyConfig(universe={"TSLA"}, allow_short=False)
    assert decide(L(["TSLA"], -0.8, 0.9), cfg) is None


def test_shorts_when_enabled():
    cfg = StrategyConfig(universe={"TSLA"}, allow_short=True)
    intent = decide(L(["TSLA"], -0.8, 0.9), cfg)
    assert intent is not None and intent.side == "sell"


def test_respects_universe():
    cfg = StrategyConfig(universe={"AAPL"})
    assert decide(L(["TSLA"], 0.9, 0.9), cfg) is None


def test_empty_universe_allows_all():
    cfg = StrategyConfig(universe=set())
    assert decide(L(["WHATEVER"], 0.9, 0.9), cfg) is not None


def test_skips_already_open_symbol():
    cfg = StrategyConfig(universe={"TSLA"})
    assert decide(L(["TSLA"], 0.9, 0.9), cfg, open_symbols={"TSLA"}) is None


def test_picks_first_qualifying_ticker_in_universe():
    cfg = StrategyConfig(universe={"MSFT"})
    intent = decide(L(["TSLA", "MSFT"], 0.9, 0.9), cfg)
    assert intent is not None and intent.symbol == "MSFT"
