"""
Groq market-context enricher.

Role in the architecture: Groq is ANOTHER feature extractor, never the trader.
Given (a) deterministic technical features from market data and (b) the news
Label, it emits a SMALL structured MarketContext object -- regime, whether the
technicals agree with the news direction, and discrete risk flags. The
deterministic strategy.confirm_intent() consumes those flags. Groq does not size
positions, pick entries, or place orders.

Everything fails OPEN: if Groq errors, times out, or there are no features, we
return a neutral context with confirm=True so the system keeps trading rather
than silently freezing. The deterministic gates still apply on top.

Uses Groq's OpenAI-compatible REST endpoint via stdlib urllib (no extra dep).
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import Optional

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM = """You are a market-context tagger for a paper-trading system. You are \
NOT the trader. You read a proposed trade (side + the news that triggered it) and \
a set of deterministic technical features, then output ONE JSON object and nothing \
else -- no prose, no markdown.

Schema:
{
  "regime": "risk_on|risk_off|neutral",
  "trend_alignment": true,        // do the technicals agree with the trade side?
  "risk_flags": ["high_volatility"],  // subset of: high_volatility, thin_volume, overextended, gap_risk, none
  "confirm": true,                // is this a coherent setup given news + technicals?
  "note": "one short clause"
}

Guidance:
- A long is trend-aligned if momentum (ret_5d/ret_20d) is not clearly negative and \
price is at/above its 20d SMA; a short is the mirror image.
- Flag high_volatility when vol_20d is elevated, thin_volume when rvol < 0.5, \
overextended when a same-direction move already looks stretched.
- Set confirm=false only when news and technicals clearly contradict or risk is high.
- Output JSON only."""


@dataclass
class MarketContext:
    regime: str = "neutral"
    trend_alignment: bool = True
    risk_flags: list[str] = field(default_factory=lambda: ["none"])
    confirm: bool = True
    note: str = ""
    source: str = "groq"

    def as_log(self) -> dict:
        d = asdict(self)
        d["risk_flags"] = "|".join(self.risk_flags) if self.risk_flags else "none"
        d["trend_alignment"] = int(self.trend_alignment)
        d["confirm"] = int(self.confirm)
        return d


def _neutral(note: str) -> MarketContext:
    return MarketContext(note=note, source="fallback")


class GroqContext:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", timeout: float = 12.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def enrich(self, side: str, label, features) -> MarketContext:
        if not self.api_key:
            return _neutral("no groq key")
        if features is None:
            return _neutral("no features")

        payload = {
            "side": side,
            "news": {
                "event_type": getattr(label, "event_type", ""),
                "sentiment": getattr(label, "sentiment", 0.0),
                "confidence": getattr(label, "confidence", 0.0),
                "tickers": getattr(label, "tickers", []),
            },
            "technicals": {
                "symbol": features.symbol,
                "last_close": features.last_close,
                "ret_5d": features.ret_5d,
                "ret_20d": features.ret_20d,
                "vol_20d": features.vol_20d,
                "rvol": features.rvol,
                "above_sma20": features.above_sma20,
            },
        }
        body = json.dumps({
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 220,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
        }).encode()

        req = urllib.request.Request(
            GROQ_URL, data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "paper-trader/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            raw = data["choices"][0]["message"]["content"]
            obj = json.loads(raw)
        except Exception as e:
            print(f"[groq] context error (fail-open): {e}")
            return _neutral("groq error")

        return _coerce(obj)


def _coerce(obj: dict) -> MarketContext:
    """Tolerant parse of the model's JSON into a MarketContext."""
    regime = str(obj.get("regime", "neutral")).lower()
    if regime not in {"risk_on", "risk_off", "neutral"}:
        regime = "neutral"
    flags = obj.get("risk_flags", ["none"])
    if isinstance(flags, str):
        flags = [flags]
    flags = [str(f) for f in flags] or ["none"]
    return MarketContext(
        regime=regime,
        trend_alignment=bool(obj.get("trend_alignment", True)),
        risk_flags=flags,
        confirm=bool(obj.get("confirm", True)),
        note=str(obj.get("note", ""))[:120],
        source="groq",
    )
