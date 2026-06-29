"""
The LLM feature-extractor. One job: read a news item, emit a structured Label.
It does NOT decide trades. Keeping the model on this side of the wall is what
lets you cache labels and replay them through the deterministic strategy in a
backtest.

Temperature is 0 and the prompt demands JSON only, so labels are as stable and
parseable as an LLM gets. parse_label() (in labels.py) tolerates extra keys and
malformed output, degrading to "no signal" rather than crashing the loop.
"""
from __future__ import annotations

from typing import Optional

from anthropic import Anthropic

from .labels import Label, parse_label

SYSTEM_PROMPT = """You are a disciplined financial-news tagger feeding a \
systematic trading model. Read ONE news item and output a SINGLE JSON object and \
nothing else -- no prose, no markdown fences.

Schema:
{
  "tickers": ["TSLA"],          // US-listed symbols the item is MATERIALLY and DIRECTLY about; [] if none
  "sentiment": 0.7,              // -1.0 very bearish .. +1.0 very bullish, for those tickers over the next hours-to-days
  "confidence": 0.6,             // 0.0 .. 1.0  probability this is genuinely tradable signal, NOT noise
  "event_type": "earnings",      // one of: earnings|guidance|M&A|legal|regulatory|product|partnership|analyst|insider|macro|noise
  "rationale": "one short clause"
}

Calibration rules (be strict -- most headlines are noise):
- confidence < 0.30 and event_type "noise" for: opinion/round-ups, vague macro color, \
already-priced or stale items, listicles, or anything not about a specific tradable company.
- Reserve confidence > 0.70 for concrete, surprising, company-specific catalysts \
(earnings beats/misses, guidance changes, M&A, FDA/legal rulings, major contracts).
- sentiment magnitude should match how decisively the news moves the named tickers; \
mixed or two-sided news -> small magnitude near 0.
- Only include tickers you are confident the item is directly about. A passing mention \
is not materiality. Prefer [] over guessing.
- Sector/index-only macro with no single tradable name -> tickers [], event_type "macro", \
keep confidence modest.
- Output JSON only."""


class Labeler:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def label(self, item: dict) -> Optional[Label]:
        content = f"HEADLINE: {item.get('title','')}\n\nSUMMARY: {item.get('summary','')}"
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                temperature=0.0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            raw = "".join(block.text for block in resp.content if block.type == "text")
        except Exception as e:  # network/api errors must not kill the loop
            print(f"[labeler] API error: {e}")
            return None
        return parse_label(raw, source_id=item.get("id", ""))
