"""
The feature-extractor. One job: read a news item, emit a structured Label.
It does NOT decide trades -- keeping the model on this side of the wall is what
lets you cache labels and replay them through the deterministic strategy.

Runs entirely on FREE models via the reasoning framework (trader/reasoner.py):
Groq -> Cloudflare -> OpenRouter, each fail-soft, with a deliberate
reason-then-emit-JSON scaffold. No paid API.
"""
from __future__ import annotations

from typing import Optional

from . import reasoner
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
    """News -> structured Label, powered by the free-model reasoning framework.

    Signature stays backward-compatible (run.py may pass legacy args); they are
    ignored -- the reasoner selects the free provider chain itself."""

    def __init__(self, *args, **kwargs):
        pass

    def label(self, item: dict) -> Optional[Label]:
        content = f"HEADLINE: {item.get('title','')}\n\nSUMMARY: {item.get('summary','')}"
        try:
            raw = reasoner.reason_json(SYSTEM_PROMPT, content, max_tokens=400, cache_ttl=600)
        except Exception as e:  # network/api errors must not kill the loop
            print(f"[labeler] reasoner error: {str(e)[:120]}")
            return None
        if not raw:
            print("[labeler] no provider returned a label -> no signal")
            return None
        return parse_label(raw, source_id=item.get("id", ""))
