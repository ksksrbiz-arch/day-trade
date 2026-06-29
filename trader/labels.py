"""
The Label is the contract between the LLM and the rest of the system.

Design intent: a Label is a PURE FUNCTION of a news item's text. That is the
whole reason the strategy is backtestable. If you archive (news_item -> Label)
pairs, you can replay history through the deterministic strategy as many times
as you like, with zero LLM calls and zero randomness. The LLM is a feature
extractor here, not the trader.

This module imports NOTHING heavy (no anthropic, no alpaca) on purpose, so the
deterministic tests can run without API keys or network.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Label:
    tickers: list[str]
    sentiment: float   # -1.0 (very bearish) .. 1.0 (very bullish)
    confidence: float  # 0.0 .. 1.0  -- how sure the model is the news is tradable
    event_type: str    # e.g. "earnings", "guidance", "M&A", "macro", "noise"
    rationale: str = ""
    source_id: str = ""


def _clamp(x: float, lo: float, hi: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, x))


def _extract_json_object(raw: str) -> Optional[str]:
    """Pull the first balanced {...} object out of a messy LLM response.

    Handles ```json fences, leading prose, trailing prose, etc.
    """
    if not raw:
        return None
    # strip code fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : i + 1]
    return None


def parse_label(raw: str, source_id: str = "") -> Optional[Label]:
    """Parse an LLM response into a validated Label, or None if unusable.

    Never raises on bad input -- a malformed label must degrade to 'no signal',
    not crash the loop.
    """
    blob = _extract_json_object(raw)
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    tickers = data.get("tickers", [])
    if isinstance(tickers, str):
        tickers = [tickers]
    if not isinstance(tickers, list):
        return None
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]

    return Label(
        tickers=tickers,
        sentiment=_clamp(data.get("sentiment", 0.0), -1.0, 1.0),
        confidence=_clamp(data.get("confidence", 0.0), 0.0, 1.0),
        event_type=str(data.get("event_type", "unknown")).strip() or "unknown",
        rationale=str(data.get("rationale", "")).strip(),
        source_id=source_id,
    )
