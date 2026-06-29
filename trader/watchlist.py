"""
Watch -> wait -> strike engine: trade like a discretionary day trader.

A catalyst (news) does NOT trigger a trade. Instead it ARMS a watch with a
thesis (long/short), a confirmation TRIGGER level, and an EXPIRY. The bot then
WATCHES price: it only STRIKES when price confirms the thesis (a long fires on a
breakout above the trigger; a short on a breakdown below). If the confirmation
never comes before expiry, the watch is dropped -- the chance didn't show.

Why this drives efficiency: it replaces "market-buy on every headline" (slippage
+ false signals) with "only act when the market agrees" -> fewer, higher-quality
entries. The trigger math is pure + tested; the manager persists to
data/watchlist.json so watches survive restarts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
WL_PATH = PROJ / "data" / "watchlist.json"


def trigger_level(thesis: str, armed_price: float, buffer: float) -> float:
    """Confirmation level. long: must break ABOVE armed*(1+buffer);
    short: must break BELOW armed*(1-buffer)."""
    b = abs(buffer)
    return round(armed_price * (1 + b), 4) if thesis == "buy" else round(armed_price * (1 - b), 4)


def triggered(thesis: str, level: float, current: float) -> bool:
    if current is None:
        return False
    return current >= level if thesis == "buy" else current <= level


def is_expired(entry: dict, now: float = None) -> bool:
    return (now or time.time()) >= entry.get("expiry_ts", 0)


class WatchList:
    def __init__(self, path: Path = WL_PATH):
        self.path = path
        self.items = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                return {}
        return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, indent=2))

    def arm(self, symbol, thesis, armed_price, catalyst, *, buffer=0.005,
            expiry_min=180, confidence=0.0, source="", sentiment=0.0):
        """Arm/refresh a watch on symbol. One active watch per symbol."""
        key = symbol.upper()
        now = time.time()
        existing = self.items.get(key)
        # don't downgrade a fresh same-thesis watch; refresh expiry instead
        if existing and existing.get("thesis") == thesis and not is_expired(existing, now):
            existing["expiry_ts"] = now + expiry_min * 60
            existing["catalyst"] = catalyst or existing.get("catalyst", "")
            self._save()
            return existing
        entry = {
            "symbol": key, "thesis": thesis, "armed_price": round(armed_price, 4),
            "trigger": trigger_level(thesis, armed_price, buffer),
            "buffer": buffer, "catalyst": (catalyst or "")[:160], "source": source,
            "confidence": round(confidence, 2), "sentiment": round(sentiment, 2),
            "created_ts": now, "expiry_ts": now + expiry_min * 60, "status": "watching",
        }
        self.items[key] = entry
        self._save()
        return entry

    def active(self) -> list[dict]:
        return list(self.items.values())

    def evaluate(self, symbol, current_price, now=None) -> str:
        """Return 'fire' | 'expired' | 'watching' and update/remove accordingly."""
        key = symbol.upper()
        e = self.items.get(key)
        if not e:
            return "none"
        if is_expired(e, now):
            del self.items[key]; self._save()
            return "expired"
        if triggered(e["thesis"], e["trigger"], current_price):
            del self.items[key]; self._save()
            return "fire"
        return "watching"

    def prune(self, now=None):
        now = now or time.time()
        drop = [k for k, e in self.items.items() if is_expired(e, now)]
        for k in drop:
            del self.items[k]
        if drop:
            self._save()
        return drop
