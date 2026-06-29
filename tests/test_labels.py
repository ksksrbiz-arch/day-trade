from trader.labels import parse_label


def test_parses_clean_json():
    raw = '{"tickers":["TSLA"],"sentiment":0.7,"confidence":0.8,"event_type":"earnings"}'
    label = parse_label(raw)
    assert label.tickers == ["TSLA"]
    assert label.sentiment == 0.7
    assert label.confidence == 0.8


def test_strips_code_fences():
    raw = '```json\n{"tickers":["AAPL"],"sentiment":0.5,"confidence":0.6,"event_type":"macro"}\n```'
    label = parse_label(raw)
    assert label.tickers == ["AAPL"]


def test_handles_leading_and_trailing_prose():
    raw = 'Sure! Here is the label:\n{"tickers":["NVDA"],"sentiment":0.9,"confidence":0.7,"event_type":"product"} Hope that helps.'
    label = parse_label(raw)
    assert label.tickers == ["NVDA"]
    assert label.event_type == "product"


def test_clamps_out_of_range_values():
    raw = '{"tickers":["X"],"sentiment":5.0,"confidence":-2.0,"event_type":"earnings"}'
    label = parse_label(raw)
    assert label.sentiment == 1.0
    assert label.confidence == 0.0


def test_uppercases_and_trims_tickers():
    raw = '{"tickers":[" tsla ","aapl"],"sentiment":0.4,"confidence":0.6,"event_type":"x"}'
    label = parse_label(raw)
    assert label.tickers == ["TSLA", "AAPL"]


def test_single_ticker_string_coerced_to_list():
    raw = '{"tickers":"TSLA","sentiment":0.4,"confidence":0.6,"event_type":"x"}'
    label = parse_label(raw)
    assert label.tickers == ["TSLA"]


def test_garbage_returns_none():
    assert parse_label("the model refused to answer") is None
    assert parse_label("") is None
    assert parse_label("{not valid json at all,,,}") is None


def test_missing_fields_get_safe_defaults():
    raw = '{"tickers":["X"]}'
    label = parse_label(raw)
    assert label.sentiment == 0.0
    assert label.confidence == 0.0
    assert label.event_type == "unknown"
