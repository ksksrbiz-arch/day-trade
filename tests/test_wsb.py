"""Tests for the WallStreetBets RSS module (no network)."""
from trader import wsb


def test_strip_html():
    assert wsb._strip("<b>Hello</b> <i>world</i>") == "Hello world"


def test_extract_cashtags():
    tks = wsb.extract_tickers("Loading up on $GME and $AMC calls", valid=None)
    assert "GME" in tks and "AMC" in tks


def test_extract_validates_bare_tokens():
    valid = {"NVDA", "WEN"}
    tks = wsb.extract_tickers("NVDA to the moon, WEN fries, YOLO into FD", valid)
    assert "NVDA" in tks and "WEN" in tks
    assert "YOLO" not in tks and "FD" not in tks   # slang filtered


def test_stoplist_excludes_slang():
    valid = {"LFG", "NVDA"}   # LFG happens to be a real ticker but is slang
    tks = wsb.extract_tickers("LFG NVDA", valid)
    assert "NVDA" in tks and "LFG" not in tks


def test_item_block_parsing():
    xml = "<item><title><![CDATA[Big news]]></title><link>http://x</link></item>"
    b = wsb._item_blocks(xml)
    assert len(b) == 1
    assert wsb._field(b[0], "title") == "Big news"
