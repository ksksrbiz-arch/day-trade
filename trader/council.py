"""
Multi-model reasoning council -- the "reconciliation brain".

Several independent models each give a STRUCTURED stance on a proposed trade,
and a DETERMINISTIC aggregator reconciles them into one auditable consensus +
recommended action. This is how the LLM layer and Omni "communicate": not by
chatting freely, but by each emitting a normalized {stance, confidence,
rationale}, which pure code then tallies.

Members (each optional / fail-soft):
  * Groq (Llama-3.3-70B) - PRIMARY reasoner (free)
  * Cohere (Command)   - third opinion
  * Cloudflare Workers AI (Llama) - fourth opinion (needs CF account + token)
  * Omni (Clear Street)- grounded in live market/portfolio data

HONEST SCOPE: an ensemble reduces single-model blind spots and gives you an
auditable, disagreement-aware signal. It does NOT predict prices or create edge.
Everything is read-only research; the deterministic strategy still decides, and
nothing here can place an order. Slow (many model calls) -> advisory/live-only,
not part of the backtestable core.
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error

from .omni import parse_stance, OmniClient, research as omni_research
from .resilience import call as _rcall

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_VAL = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}

PROMPT = (
    "You are one member of a trading research council. Give a disciplined read on a "
    "{side} in {symbol} over the next few days.{ctx}\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "STANCE: <BULLISH|BEARISH|NEUTRAL>\nCONFIDENCE: <0.0-1.0>\nREASON: <one short sentence>"
)


def _parse(text: str) -> dict:
    stance = parse_stance(text)
    m = re.search(r"stance:\s*(bullish|bearish|neutral)", text, re.I)
    if m:
        stance = m.group(1).lower()
    conf = 0.5
    c = re.search(r"confidence:\s*([01](?:\.\d+)?)", text, re.I)
    if c:
        try:
            conf = max(0.0, min(1.0, float(c.group(1))))
        except ValueError:
            pass
    r = re.search(r"reason:\s*(.+)", text, re.I)
    reason = (r.group(1).strip() if r else text.strip())[:160]
    return {"stance": stance, "confidence": round(conf, 2), "rationale": reason}


def _http(url, headers, body, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---- providers (each returns {source, ok, stance, confidence, rationale}) ----

def _groq(cfg, prompt):
    d = _http("https://api.groq.com/openai/v1/chat/completions",
              {"Authorization": f"Bearer {cfg.groq_key}", "Content-Type": "application/json", "User-Agent": _UA},
              {"model": cfg.groq_model, "temperature": 0.0, "max_tokens": 120,
               "messages": [{"role": "user", "content": prompt}]})
    return _parse(d["choices"][0]["message"]["content"])


def _cohere(cfg, prompt):
    d = _http("https://api.cohere.com/v2/chat",
              {"Authorization": f"Bearer {cfg.cohere_key}", "Content-Type": "application/json", "User-Agent": _UA},
              {"model": cfg.cohere_model, "messages": [{"role": "user", "content": prompt}]})
    parts = d.get("message", {}).get("content", [])
    txt = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return _parse(txt)


def _cloudflare(cfg, prompt):
    url = f"https://api.cloudflare.com/client/v4/accounts/{cfg.cf_account_id}/ai/run/{cfg.cf_model}"
    d = _http(url, {"Authorization": f"Bearer {cfg.cf_api_token}", "Content-Type": "application/json", "User-Agent": _UA},
              {"messages": [{"role": "user", "content": prompt}], "max_tokens": 120})
    txt = (d.get("result") or {}).get("response", "") if isinstance(d.get("result"), dict) else str(d.get("result", ""))
    return _parse(txt)


def _omni(cfg, symbol, side):
    cli = OmniClient(cfg.clearstreet_token, cfg.cs_account_id)
    r = omni_research(cli, symbol, side)
    return {"stance": r.get("stance", "neutral"), "confidence": 0.6 if r.get("ok") else 0.3,
            "rationale": r.get("text", "")[:160]}


def _replicate(cfg, prompt):
    url = f"https://api.replicate.com/v1/models/{cfg.replicate_model}/predictions"
    d = _http(url, {"Authorization": f"Token {cfg.replicate_key}", "Content-Type": "application/json",
                    "Prefer": "wait", "User-Agent": _UA},
              {"input": {"prompt": prompt, "max_tokens": 120}}, timeout=60)
    out = d.get("output", "")
    if isinstance(out, list):
        out = "".join(str(x) for x in out)
    return _parse(str(out))


# ---- deterministic aggregation + actionability (pure, tested) ----

def aggregate(votes: list[dict], weights: dict | None = None) -> dict:
    """Reconcile member votes into a consensus. Pure.
    votes: [{source,stance,confidence,rationale}]; weights: source->reliability
    multiplier (learned from realized outcomes; defaults to 1.0 each)."""
    live = [v for v in votes if v.get("stance") in _VAL]
    if not live:
        return {"consensus": "neutral", "score": 0.0, "agreement": 0.0, "n": 0,
                "bull": 0, "bear": 0, "neutral": 0, "dissent": []}
    w = weights or {}
    def _cw(v):
        return v["confidence"] * w.get(v.get("source"), 1.0)
    wsum = sum(_cw(v) for v in live) or 1.0
    score = sum(_VAL[v["stance"]] * _cw(v) for v in live) / wsum
    bull = sum(1 for v in live if v["stance"] == "bullish")
    bear = sum(1 for v in live if v["stance"] == "bearish")
    neu = sum(1 for v in live if v["stance"] == "neutral")
    if score >= 0.2:
        consensus = "bullish"
    elif score <= -0.2:
        consensus = "bearish"
    else:
        consensus = "neutral"
    agree_n = {"bullish": bull, "bearish": bear, "neutral": neu}[consensus]
    agreement = round(agree_n / len(live), 2)
    dissent = [v["source"] for v in live if v["stance"] != consensus and v["stance"] != "neutral"]
    return {"consensus": consensus, "score": round(score, 3), "agreement": agreement,
            "n": len(live), "bull": bull, "bear": bear, "neutral": neu, "dissent": dissent}


def decide(side: str, agg: dict, min_agreement: float = 0.6, regime: str = None) -> dict:
    if regime == "high_vol":
        min_agreement = max(min_agreement, 0.75)  # stressed tape -> demand stronger consensus
    """Map consensus -> bounded action for a proposed side. Pure.
    Returns {action, reason}. action in {proceed, caution, veto, no_signal}."""
    if agg["n"] == 0:
        return {"action": "no_signal", "reason": "no council votes"}
    cons = agg["consensus"]
    aligned = (side == "buy" and cons == "bullish") or (side == "sell" and cons == "bearish")
    opposed = (side == "buy" and cons == "bearish") or (side == "sell" and cons == "bullish")
    if opposed and agg["agreement"] >= min_agreement:
        return {"action": "veto", "reason": f"council {cons} ({agg['agreement']:.0%}) opposes {side}"}
    if aligned and agg["agreement"] >= min_agreement:
        return {"action": "proceed", "reason": f"council {cons} ({agg['agreement']:.0%}) backs {side}"}
    return {"action": "caution", "reason": f"council mixed (score {agg['score']:+.2f}, {agg['agreement']:.0%} agree)"}


def convene(cfg, symbol: str, side: str, context: str = "") -> dict:
    """Run the full council live. Fail-soft per member. Returns votes + consensus + action."""
    from . import market_brain
    _regime = market_brain.cached_regime("neutral")
    _rn = (" Market regime is HIGH-VOLATILITY: down-weight short-term momentum (it traps entries here); up-weight mean-reversion and volatility-cluster factors." if _regime == "high_vol" else (f" Market regime is {_regime}." if _regime in ("risk_on", "risk_off") else ""))
    ctx = (f" Context: {context}" if context else "") + _rn
    try:
        from . import awareness as _aw
        _b = _aw.brief(8)
        if _b:
            ctx += " Unified desk awareness (all layers): " + _b.replace(chr(10), ' | ')[:650]
    except Exception:  # noqa: BLE001
        pass
    try:
        from .ml import infer as _ml
        _ms = _ml.score_symbol(symbol)
        if _ms is not None:
            _imp = _ml.model_card().get("importances", {})
            _top = ", ".join(list(_imp)[:3])
            ctx += (f" The in-house ML model scores this name {_ms:+.2f} on a -1..+1 scale"
                    f" (key drivers: {_top}); factor it in but reason independently.")
    except Exception:  # noqa: BLE001
        pass
    prompt = PROMPT.format(side=("long" if side == "buy" else "short"), symbol=symbol, ctx=ctx)
    members = []
    plan = []
    if cfg.groq_key:
        plan.append(("groq", lambda: _groq(cfg, prompt)))
    if cfg.cohere_key:
        plan.append(("cohere", lambda: _cohere(cfg, prompt)))
    if cfg.replicate_key:
        plan.append(("replicate", lambda: _replicate(cfg, prompt)))
    if cfg.cf_account_id and cfg.cf_api_token:
        plan.append(("cloudflare", lambda: _cloudflare(cfg, prompt)))
    if cfg.openrouter_key:
        plan.append(("openrouter", lambda: _openrouter(cfg, prompt)))
    if cfg.clearstreet_token and cfg.cs_account_id:
        plan.append(("omni", lambda: _omni(cfg, symbol, side)))
    for name, fn in plan:
        try:
            _fb = (lambda: _groq(cfg, prompt)) if (name != "groq" and cfg.groq_key) else None
            _r = _rcall(fn, kind="llm", budget_s=40, fallback=_fb)
            if not _r["ok"]:
                raise RuntimeError(_r["error"] or "failed")
            v = _r["value"]; v["source"] = name; v["ok"] = True
            v["attempts"] = _r["attempts"]; v["fell_back"] = _r["fell_back"]
            members.append(v)
        except Exception as e:
            members.append({"source": name, "ok": False, "stance": None,
                            "confidence": 0.0, "rationale": str(e)[:120]})
    try:
        from .ml import agent_reliability as _ar
        _ar.log_votes(symbol, side, members)
        _w = _ar.weights()
    except Exception:  # noqa: BLE001
        _w = None
    agg = aggregate(members, _w)
    act = decide(side, agg, regime=_regime)
    try:
        from . import mesh as _mesh
        _mesh.publish('council', 'consensus',
                      f"{symbol} {side}: council {agg['consensus']} ({agg['agreement']:.0%}, score {agg['score']:+.2f}) -> {act['action']}",
                      symbol=symbol, salience=0.6)
    except Exception:  # noqa: BLE001
        pass
    return {"symbol": symbol, "side": side, "members": members, **agg, "decision": act}


# ---- OpenRouter (free models, with fallback rotation) ----

_OR_URL = "https://openrouter.ai/api/v1/chat/completions"


def _or_chat(cfg, prompt: str, max_tokens: int = 160) -> str:
    """Try each configured free model until one responds. Raises if all fail."""
    models = list(cfg.openrouter_models) or ["openai/gpt-oss-20b:free"]
    last = None
    for m in models:
        try:
            d = _http(_OR_URL,
                      {"Authorization": f"Bearer {cfg.openrouter_key}", "Content-Type": "application/json",
                       "User-Agent": _UA, "X-Title": "paper-trader-council"},
                      {"model": m, "max_tokens": max_tokens, "temperature": 0.2,
                       "messages": [{"role": "user", "content": prompt}]}, timeout=45)
            return d["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"all OpenRouter free models failed: {last}")


def _openrouter(cfg, prompt):
    return _parse(_or_chat(cfg, prompt, max_tokens=120))


def openrouter_free_models(cfg, limit: int = 40) -> list[str]:
    """List currently-free OpenRouter models (pricing.prompt == 0)."""
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                     headers={"Authorization": f"Bearer {cfg.openrouter_key}", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        free = [m["id"] for m in d.get("data", [])
                if str(m.get("pricing", {}).get("prompt", "1")) in ("0", "0.0")]
        return sorted(free)[:limit]
    except Exception as e:
        return [f"(error: {e})"]


# ---- free-form chat per member (for deliberation) ----

def _member_text(cfg, name: str, prompt: str) -> str:
    if name == "groq":
        d = _http("https://api.groq.com/openai/v1/chat/completions",
                  {"Authorization": f"Bearer {cfg.groq_key}", "Content-Type": "application/json", "User-Agent": _UA},
                  {"model": cfg.groq_model, "max_tokens": 400, "temperature": 0.3,
                   "messages": [{"role": "user", "content": prompt}]})
        return d["choices"][0]["message"]["content"]
    if name == "cohere":
        d = _http("https://api.cohere.com/v2/chat",
                  {"Authorization": f"Bearer {cfg.cohere_key}", "Content-Type": "application/json", "User-Agent": _UA},
                  {"model": cfg.cohere_model, "messages": [{"role": "user", "content": prompt}]})
        return " ".join(p.get("text", "") for p in d.get("message", {}).get("content", []) if p.get("type") == "text")
    if name == "cloudflare":
        url = f"https://api.cloudflare.com/client/v4/accounts/{cfg.cf_account_id}/ai/run/{cfg.cf_model}"
        d = _http(url, {"Authorization": f"Bearer {cfg.cf_api_token}", "Content-Type": "application/json", "User-Agent": _UA},
                  {"messages": [{"role": "user", "content": prompt}], "max_tokens": 400})
        res = d.get("result", {})
        if isinstance(res, dict):
            return res.get("response") or (res.get("choices", [{}])[0].get("message", {}).get("content", ""))
        return str(res)
    if name == "openrouter":
        return _or_chat(cfg, prompt, max_tokens=400)
    raise ValueError(name)


def deliberate(cfg, question: str, symbol: str = "") -> dict:
    """Council CHAT with cross-talk: members answer independently, Omni grounds
    the discussion in LIVE market/account data (its 'tool'), then a chair
    synthesizes one best answer that weighs every voice. Returns transcript +
    final. Read-only; the chair is told never to place orders or invent data."""
    focus = f" Focus ticker/context: {symbol}." if symbol else ""
    ask = (f"You are a member of a trading research council. Answer the user's question "
           f"concisely and honestly (3-5 sentences), flagging uncertainty.{focus}\n\nQUESTION: {question}")
    members = []
    text_members = []
    if cfg.groq_key: text_members.append("groq")
    if cfg.cohere_key: text_members.append("cohere")
    if cfg.cf_account_id and cfg.cf_api_token: text_members.append("cloudflare")
    if cfg.openrouter_key: text_members.append("openrouter")
    for name in text_members:
        try:
            members.append({"source": name, "ok": True, "text": _member_text(cfg, name, ask)[:1200]})
        except Exception as e:
            members.append({"source": name, "ok": False, "text": str(e)[:160]})
    # Omni grounds the council in live data (its tool)
    if cfg.clearstreet_token and cfg.cs_account_id:
        try:
            cli = OmniClient(cfg.clearstreet_token, cfg.cs_account_id)
            r = cli.ask(question + (f" (focus {symbol})" if symbol else ""))
            members.append({"source": "omni", "ok": not r.get("error"),
                            "text": (r.get("text") or r.get("error") or "")[:1200]})
        except Exception as e:
            members.append({"source": "omni", "ok": False, "text": str(e)[:160]})

    pooled = "\n\n".join(f"[{m['source']}]: {m['text']}" for m in members if m.get("ok") and m.get("text"))
    chair_prompt = (
        "You are the CHAIR of a multi-model trading research council. Below are your "
        "members' responses; the [omni] entry is grounded in LIVE Clear Street market & "
        "account data. Synthesize ONE clear, honest answer for the user: integrate the "
        "strongest points, explicitly note where members agree and disagree, prefer the "
        "live-data-grounded facts from omni when there's a factual conflict, and never "
        "invent numbers or recommend placing an order. Keep it tight.\n\n"
        f"USER QUESTION: {question}\n\nMEMBER RESPONSES:\n{pooled}\n\nSYNTHESIS:")
    chair = text_members[0] if text_members else None
    final = ""
    if chair:
        try:
            final = _member_text(cfg, chair, chair_prompt).strip()
        except Exception as e:
            final = f"(synthesis failed: {e})"
    return {"question": question, "symbol": symbol, "final": final,
            "chair": chair, "members": members}
