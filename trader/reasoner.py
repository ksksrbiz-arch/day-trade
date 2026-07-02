"""
Reasoning framework over FREE models -- gives Groq / Cloudflare / OpenRouter a
structured, deliberate reasoning protocol so they serve as the platform's
PRIMARY reasoner in place of a paid API. No Anthropic, no paid calls.

Provider chain (each fail-soft, browser UA to avoid WAF blocks):
    Groq Llama-3.3-70B  ->  Cloudflare Workers AI  ->  OpenRouter free models

The quality lever is *scaffolding*, not model size: every reasoning call asks the
model to plan -> reason step by step -> self-critique/revise -> commit, which
closes much of the gap with a frontier model on the structured, verifiable tasks
this system needs (news labeling, trade-stance reasoning, macro narration).

API:
    complete(system, user, json_mode=False)      -> str   (raw provider text, fallback chain)
    reason(task, system="")                       -> str   (deliberate; returns FINAL answer)
    reason_json(system, user)                     -> str   (deliberate; returns JSON-only text)
    reason_consistent(system, user, n=3)          -> str   (self-consistency majority vote; opt-in)
    available()                                   -> bool
    active_provider()                             -> str
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# The Claude-style deliberation scaffold (kept internal; only FINAL is returned).
REASON_SCAFFOLD = (
    "Reason like a careful, sceptical expert. Think it through privately in a scratchpad:\n"
    "1) Restate the task in your own words.\n"
    "2) List the key facts and considerations that actually matter.\n"
    "3) Reason step by step toward an answer.\n"
    "4) Self-critique: check for errors, missing information, over-confidence, or bias; revise.\n"
    "5) Commit to a single best answer.\n"
    "Output ONLY the final answer on a line beginning with 'FINAL:' -- do not reveal the scratchpad."
)
JSON_SCAFFOLD = (
    "Reason privately in a scratchpad (restate -> consider -> step-by-step -> self-critique -> commit), "
    "then output ONLY the final JSON object. No prose, no markdown fences, no scratchpad -- JSON only."
)


def _env(cfg=None):
    if cfg is not None:
        g = lambda a, d="": getattr(cfg, a, d) or d
        return {
            "groq_key": g("groq_key"), "groq_model": g("groq_model", "llama-3.3-70b-versatile"),
            "cf_account": g("cf_account_id"), "cf_token": g("cf_api_token"),
            "cf_model": g("cf_model", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
            "or_key": g("openrouter_key"),
            "or_models": list(getattr(cfg, "openrouter_models", ()) or ()) or ["openai/gpt-oss-120b:free"],
        }
    return {
        "groq_key": os.getenv("GROQ_API_KEY", ""),
        "groq_model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "cf_account": os.getenv("CF_ACCOUNT_ID", ""), "cf_token": os.getenv("CF_API_TOKEN", ""),
        "cf_model": os.getenv("CF_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        "or_key": os.getenv("OPENROUTER_API_KEY", ""),
        "or_models": [m.strip() for m in os.getenv("OPENROUTER_MODELS", "").split(",") if m.strip()]
                     or ["openai/gpt-oss-120b:free"],
    }


def _http_json(url, headers, body, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**headers, "User-Agent": _UA}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _groq(e, system, user, json_mode, max_tokens, temperature):
    body = {"model": e["groq_model"], "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    d = _http_json("https://api.groq.com/openai/v1/chat/completions",
                   {"Authorization": f"Bearer {e['groq_key']}", "Content-Type": "application/json"}, body)
    return d["choices"][0]["message"]["content"]


def _cloudflare(e, system, user, json_mode, max_tokens, temperature):
    url = f"https://api.cloudflare.com/client/v4/accounts/{e['cf_account']}/ai/run/{e['cf_model']}"
    d = _http_json(url, {"Authorization": f"Bearer {e['cf_token']}", "Content-Type": "application/json"},
                   {"max_tokens": max_tokens, "temperature": temperature,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    res = d.get("result", {})
    if isinstance(res, dict):
        return res.get("response") or (res.get("choices", [{}])[0].get("message", {}).get("content", ""))
    return str(res)


def _openrouter(e, system, user, json_mode, max_tokens, temperature):
    last = None
    for m in e["or_models"]:
        try:
            d = _http_json("https://openrouter.ai/api/v1/chat/completions",
                           {"Authorization": f"Bearer {e['or_key']}", "Content-Type": "application/json",
                            "X-Title": "paper-trader-reasoner"},
                           {"model": m, "max_tokens": max_tokens, "temperature": temperature,
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}]})
            return d["choices"][0]["message"]["content"]
        except Exception as ex:  # noqa: BLE001
            last = ex
            continue
    raise RuntimeError(f"all OpenRouter free models failed: {last}")


def _providers(e):
    p = []
    if e["groq_key"]:
        p.append(("groq", _groq))
    if e["cf_account"] and e["cf_token"]:
        p.append(("cloudflare", _cloudflare))
    if e["or_key"]:
        p.append(("openrouter", _openrouter))
    return p


_LAST_PROVIDER = {"name": ""}


def complete(system: str, user: str, json_mode: bool = False, max_tokens: int = 500,
             temperature: float = 0.2, cfg=None) -> str:
    """Chat completion across the free-model fallback chain. Returns '' if all fail."""
    e = _env(cfg)
    for name, fn in _providers(e):
        try:
            out = fn(e, system, user, json_mode, max_tokens, temperature)
            if out and out.strip():
                _LAST_PROVIDER["name"] = name
                return out.strip()
        except Exception as ex:  # noqa: BLE001
            print(f"[reasoner] {name} failed, trying next: {str(ex)[:110]}")
            continue
    return ""


def _final(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"FINAL:\s*(.*)", text, re.S | re.I)
    return (m.group(1).strip() if m else text.strip())


def reason(task: str, system: str = "", max_tokens: int = 600, temperature: float = 0.3, cfg=None) -> str:
    """Deliberate single-pass reasoning; returns the committed FINAL answer text."""
    sys = (system + "\n\n" + REASON_SCAFFOLD).strip()
    return _final(complete(sys, task, max_tokens=max_tokens, temperature=temperature, cfg=cfg))


def reason_json(system: str, user: str, max_tokens: int = 500, cfg=None) -> str:
    """Deliberate reasoning that returns JSON-only text (caller parses)."""
    sys = (system + "\n\n" + JSON_SCAFFOLD).strip()
    return complete(sys, user, json_mode=True, max_tokens=max_tokens, temperature=0.0, cfg=cfg)


def reason_consistent(system: str, user: str, n: int = 3, max_tokens: int = 500, cfg=None) -> str:
    """Self-consistency: sample n times at higher temperature, majority-vote the
    normalized answer. Opt-in for high-stakes calls."""
    sys = (system + "\n\n" + REASON_SCAFFOLD).strip()
    answers = []
    for _ in range(max(1, n)):
        a = _final(complete(sys, user, max_tokens=max_tokens, temperature=0.6, cfg=cfg))
        if a:
            answers.append(a)
    if not answers:
        return ""
    norm = [re.sub(r"\s+", " ", a.lower()).strip()[:400] for a in answers]
    winner = Counter(norm).most_common(1)[0][0]
    for a in answers:
        if re.sub(r"\s+", " ", a.lower()).strip()[:400] == winner:
            return a
    return answers[0]


def active_provider() -> str:
    return _LAST_PROVIDER["name"]


def available(cfg=None) -> bool:
    return bool(_providers(_env(cfg)))


if __name__ == "__main__":
    print("providers:", [n for n, _ in _providers(_env())])
    print("reason:", reason("Is 'Nvidia raises full-year guidance after record data-center revenue' bullish or bearish for NVDA? Answer in one word."))
    print("json:", reason_json("You output a JSON object {\"sentiment\": <float -1..1>}.",
                               "Apple misses earnings and cuts guidance."))
