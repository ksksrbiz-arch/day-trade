"""Tests for the watch->wait->strike engine (pure trigger + manager)."""
import time
from pathlib import Path
from trader.watchlist import trigger_level, triggered, is_expired, WatchList


def test_trigger_level_long_short():
    assert trigger_level("buy", 100.0, 0.005) == 100.5
    assert trigger_level("sell", 100.0, 0.005) == 99.5


def test_triggered_long_breakout():
    assert triggered("buy", 100.5, 100.6) is True
    assert triggered("buy", 100.5, 100.4) is False


def test_triggered_short_breakdown():
    assert triggered("sell", 99.5, 99.4) is True
    assert triggered("sell", 99.5, 99.6) is False


def test_expiry():
    assert is_expired({"expiry_ts": time.time() - 1}) is True
    assert is_expired({"expiry_ts": time.time() + 100}) is False


def _wl(tmp):
    return WatchList(path=Path(tmp) / "wl.json")


def test_arm_and_fire(tmp_path):
    wl = _wl(tmp_path)
    wl.arm("AAPL", "buy", 200.0, "earnings beat", buffer=0.005, expiry_min=60)
    assert wl.evaluate("AAPL", 200.0) == "watching"   # not yet above trigger 201.0
    assert wl.evaluate("AAPL", 201.5) == "fire"        # breakout confirmed
    assert wl.active() == []                            # removed after fire


def test_arm_and_expire(tmp_path):
    wl = _wl(tmp_path)
    e = wl.arm("MSFT", "buy", 400.0, "news", expiry_min=60)
    e["expiry_ts"] = time.time() - 1                    # force expiry
    wl.items["MSFT"] = e
    assert wl.evaluate("MSFT", 999.0) == "expired"


def test_one_watch_per_symbol_refresh(tmp_path):
    wl = _wl(tmp_path)
    wl.arm("NVDA", "buy", 100.0, "c1", expiry_min=60)
    t1 = wl.items["NVDA"]["expiry_ts"]
    time.sleep(0.01)
    wl.arm("NVDA", "buy", 100.0, "c2", expiry_min=120)   # same thesis -> refresh expiry
    assert wl.items["NVDA"]["expiry_ts"] > t1
    assert len(wl.active()) == 1


def test_prune(tmp_path):
    wl = _wl(tmp_path)
    wl.arm("X", "buy", 10.0, "c", expiry_min=60)
    wl.items["X"]["expiry_ts"] = time.time() - 1
    assert "X" in wl.prune()
