"""Rich Cloudflare Workers AI client -- uses several of the account's models.

  chat()      -> reasoning (70B instruct, fp8-fast)
  embed()     -> bge-base-en-v1.5 vectors (semantic memory / RAG)
  sentiment() -> distilbert-sst-2 (fast headline polarity)
  summarize() -> via a small llama model (bart-cnn is deprecated on the API)

All calls are fail-soft: errors return safe defaults so the autonomy loop never
crashes on a transient API hiccup.
"""
from __future__ import annotations

import json
import os
import urllib.request

UA = "Mozilla/5.0 (paper-trader agents)"
MODELS = {
    "reason": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "fast":   "@cf/meta/llama-3.1-8b-instruct",
    "embed":  "@cf/baai/bge-base-en-v1.5",
    "sentiment": "@cf/huggingface/distilbert-sst-2-int8",
}


def _acct():
    return os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_API_TOKEN", "")


def _run(model: str, payload: dict, timeout: int = 40):
    acc, tok = _acct()
    if not acc or not tok:
        raise RuntimeError("no Cloudflare creds")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def available() -> bool:
    acc, tok = _acct()
    return bool(acc and tok)


def chat(messages, model: str | None = None, max_tokens: int = 400,
         temperature: float = 0.3) -> str:
    """Return assistant text. `messages` = [{role,content}] or a bare prompt str."""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    try:
        d = _run(model or MODELS["reason"],
                 {"messages": messages, "max_tokens": max_tokens,
                  "temperature": temperature})
        res = d.get("result", {})
        if isinstance(res, dict):
            if "response" in res:
                resp = res["response"]
                return resp if isinstance(resp, str) else json.dumps(resp)
            ch = res.get("choices")
            if ch:
                c = ch[0].get("message", {}).get("content", "")
                return c if isinstance(c, str) else json.dumps(c)
        return res if isinstance(res, str) else json.dumps(res)
    except Exception as e:  # noqa: BLE001
        return f"[cf chat error: {str(e)[:80]}]"


def embed(texts: list[str]) -> list[list[float]]:
    if isinstance(texts, str):
        texts = [texts]
    try:
        d = _run(MODELS["embed"], {"text": texts})
        return d.get("result", {}).get("data", [])
    except Exception:  # noqa: BLE001
        return []


def sentiment(text: str) -> float:
    """Return polarity in [-1,1] (POSITIVE prob - NEGATIVE prob)."""
    try:
        d = _run(MODELS["sentiment"], {"text": text})
        res = d.get("result", [])
        pos = next((x["score"] for x in res if x.get("label") == "POSITIVE"), 0.5)
        neg = next((x["score"] for x in res if x.get("label") == "NEGATIVE"), 0.5)
        return round(float(pos - neg), 3)
    except Exception:  # noqa: BLE001
        return 0.0


def summarize(text: str, max_tokens: int = 120) -> str:
    return chat([{"role": "user",
                  "content": "Summarize in 2 sentences:\n" + text[:3000]}],
                model=MODELS["fast"], max_tokens=max_tokens)


if __name__ == "__main__":
    print("available:", available())
    print("chat:", chat("Reply with one word: ready")[:60])
    v = embed(["risk-off regime", "stocks rally"])
    print("embed dims:", [len(x) for x in v])
    print("sentiment+:", sentiment("shares surged on blowout earnings"))
    print("sentiment-:", sentiment("company files for bankruptcy, stock collapses"))
