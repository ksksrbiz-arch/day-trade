"""
The LLM feature-extractor. One job: read a news item, emit a structured Label.
It does NOT decide trades. Keeping the model on this side of the wall is what
lets you cache labels and replay them through the deterministic strategy in a
backtest.

Provider-agnostic + fail-soft: a fallback chain (Anthropic -> Groq -> Cloudflare)
means one provider being down or out of credit never kills the signal path.
Temperature is 0 and the prompt demands JSON only, so labels stay stable and
parseable. parse_label() (in labels.py) tolerates malformed output, degrading to
"no signal" rather than crashing the loop.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from .labels import Label, parse_label

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def _http_json(url, headers, body, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**headers, "User-Agent": _UA}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


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
    """News -> structured Label feature extractor with a fail-soft provider chain.

        Anthropic (if key)  ->  Groq (free)  ->  Cloudflare Workers AI (free)

    The first provider that returns a parseable label wins."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 groq_key: str | None = None, groq_model: str | None = None,
                 cf_account: str | None = None, cf_token: str | None = None,
                 cf_model: str | None = None):
        self.api_key = api_key
        self.model = model
        # free-provider fallbacks default from the environment (same keys the
        # council/mesh already use), so run.py needs no changes.
        self.groq_key = groq_key if groq_key is not None else os.getenv("GROQ_API_KEY", "")
        self.groq_model = groq_model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.cf_account = cf_account if cf_account is not None else os.getenv("CF_ACCOUNT_ID", "")
        self.cf_token = cf_token if cf_token is not None else os.getenv("CF_API_TOKEN", "")
        self.cf_model = cf_model or os.getenv("CF_MODEL", "@cf/meta/llama-3.1-8b-instruct")

    def _anthropic(self, content: str) -> str:
        from anthropic import Anthropic
        cl = Anthropic(api_key=self.api_key)
        resp = cl.messages.create(model=self.model, max_tokens=300, temperature=0.0,
                                  system=SYSTEM_PROMPT,
                                  messages=[{"role": "user", "content": content}])
        return "".join(b.text for b in resp.content if b.type == "text")

    def _groq(self, content: str) -> str:
        d = _http_json("https://api.groq.com/openai/v1/chat/completions",
                       {"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
                       {"model": self.groq_model, "temperature": 0.0, "max_tokens": 300,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                     {"role": "user", "content": content}]})
        return d["choices"][0]["message"]["content"]

    def _cloudflare(self, content: str) -> str:
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account}/ai/run/{self.cf_model}"
        d = _http_json(url, {"Authorization": f"Bearer {self.cf_token}", "Content-Type": "application/json"},
                       {"max_tokens": 300, "temperature": 0.0,
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                     {"role": "user", "content": content}]})
        res = d.get("result", {})
        return res.get("response", "") if isinstance(res, dict) else str(res)

    def label(self, item: dict) -> Optional[Label]:
        content = f"HEADLINE: {item.get('title','')}\n\nSUMMARY: {item.get('summary','')}"
        providers = []
        if self.api_key:
            providers.append(("anthropic", self._anthropic))
        if self.groq_key:
            providers.append(("groq", self._groq))
        if self.cf_account and self.cf_token:
            providers.append(("cloudflare", self._cloudflare))
        for name, fn in providers:
            try:
                raw = fn(content)
            except Exception as e:  # try next provider (credit/quota/network)
                print(f"[labeler] {name} failed, trying next: {str(e)[:120]}")
                continue
            label = parse_label(raw, source_id=item.get("id", ""))
            if label is not None:
                return label
        print("[labeler] all providers failed/empty -> no signal")
        return None
