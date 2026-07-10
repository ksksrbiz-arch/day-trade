"""Autonomous orchestrator.

Each agent runs on its OWN model, observes a shared blackboard (the others'
latest observations + system state + its semantic memory), reasons independently,
and chooses ONE tool to call this turn. Read tools run freely; mutating tools go
through the governor. Everything is logged to the activity feed and memory.

Run one pass:        python -m trader.agents.orchestrator
Run continuously:    python -m trader.agents.orchestrator --loop --every 900
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import json
import os
import re
import sys
import time
import urllib.request

from . import tools, governor, memory, cloudflare as cf

# Each agent: role, provider, allowed tools, mandate.
ROSTER = [
    {"name": "Quant Researcher", "provider": "cloudflare",
     "tools": ["latest_backtest", "run_backtest", "confluence", "propose_param"],
     "mandate": "Improve out-of-sample edge vs SPY. Run a backtest to measure edge; if it is "
                "weak or negative, IMMEDIATELY propose_param to raise selectivity -- "
                "e.g. CONFLUENCE_MIN_SCORE up toward 0.30-0.40, or CONFLUENCE_MIN_AGREE to 3."},
    {"name": "ML Engineer", "provider": "cloudflare",
     "tools": ["ml_card", "retrain_ml", "reconcile_agents"],
     "mandate": "Keep the ML model fresh and the agent-reliability current. "
                "Retrain when stale; reconcile council agents vs outcomes."},
    {"name": "Risk Officer", "provider": "groq",
     "tools": ["brain_state", "recall", "propose_param"],
     "mandate": "Protect capital. In high_vol or risk_off regimes you MUST act: propose_param to "
                "raise MIN_CONFIDENCE (toward 0.65-0.75) or lengthen COOLDOWN_MIN (toward 60-90)."},
    {"name": "Macro Analyst", "provider": "cloudflare",
     "tools": ["news_sentiment", "wsb_buzz", "brain_state", "confluence", "web_search"],
     "mandate": "Read the tape and headlines. Use web_search to look up CURRENT events, "
                "catalysts, or anything you are unsure about. Report whether macro "
                "supports or opposes current positioning."},
    {"name": "Strategy Critic", "provider": "vercel", "model": "openai/gpt-4o-mini",
     "tools": ["latest_backtest", "ml_card", "recall", "propose_param", "web_search"],
     "mandate": "Independently critique the desk. If backtest edge is negative or the "
                "ML edge is thin, propose a bounded selectivity change and explain the risk. "
                "Use web_search to check whether current conditions justify the current stance."},
    {"name": "Performance Auditor", "provider": "cloudflare",
     "tools": ["edge_report", "attribution", "voices_state", "mute_voice", "pin_voice"],
     "mandate": "Self-tune the confluence ensemble from REALIZED forward performance. "
                "Read edge_report + attribution + voices_state. Act ONLY on matured "
                "evidence: mute_voice a voice whose attribution verdict is 'unprofitable', "
                "or pin_voice a voice whose verdict is 'profitable' (lock its weight). "
                "The tools are guarded and will refuse premature changes, so if every "
                "voice is still 'maturing', do NOT attempt a mute/pin — instead read "
                "attribution and report which voice is closest to a verdict."},
]

_SYS = (
    "You are {name}, an autonomous trading-desk agent. Mandate: {mandate}\n"
    "BIAS TO ACTION: you are not a commentator. If the state shows a weakness "
    "(thin ML edge, negative backtest edge, stressed regime, drawdown), you MUST "
    "take a corrective ACTION this turn -- prefer propose_param / run_backtest / "
    "retrain_ml over read-only tools. Only choose a read tool when you genuinely "
    "lack the evidence to act. Recent desk turns are on the blackboard; do not "
    "merely repeat an observation already made -- escalate to an action.\n"
    "When proposing a param, pick a concrete in-bounds value and justify it.\n"
    "You may call exactly ONE tool this turn. Available tools:\n{catalog}\n"
    "Reply with STRICT JSON only: "
    '{{"thought": "<one sentence>", "tool": "<tool_name>", "args": {{...}}}}. '
    "Use {{}} for args if none. Choose the single most useful tool given the state. "
    "Output ONLY the raw JSON object on one line -- no markdown, no code fences, no prose."
)


def _groq_complete(prompt: str, timeout: int = 30) -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return ""
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    body = json.dumps({"model": model, "temperature": 0.2,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 300}).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        return d["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        return f"[groq error {str(e)[:60]}]"


def _vercel_complete(prompt: str, model: str = None, timeout: int = 30) -> str:
    key = os.environ.get("VERCEL_AI_GATEWAY_KEY", "")
    if not key:
        return ""
    model = model or os.environ.get("VERCEL_MODEL", "openai/gpt-4o-mini")
    body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": 300,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://ai-gateway.vercel.sh/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        return d["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        return f"[vercel error {str(e)[:60]}]"

def _complete(provider: str, prompt: str, model: str = None) -> str:
    # Route every agent through the unified reasoner (Groq -> Cloudflare ->
    # OpenRouter, JSON mode) so each reliably returns a parseable {thought,tool,
    # args} action -- fixes agents that previously returned no tool. Legacy
    # provider calls remain as a last-resort fallback.
    try:
        from .. import reasoner
        out = reasoner.complete(
            "You are a disciplined trading-desk agent. Respond with ONLY the "
            "requested JSON action object -- no prose, no code fences.",
            prompt, json_mode=True, max_tokens=300, temperature=0.2)
        if out and out.strip():
            return out
    except Exception:  # noqa: BLE001
        pass
    if provider == "groq":
        return _groq_complete(prompt)
    if provider == "vercel":
        return _vercel_complete(prompt, model)
    return cf.chat(prompt, max_tokens=300)


def _parse_action(txt: str) -> dict:
    if not txt:
        return {}
    t = txt.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        pass
    # scan for the first balanced {...} object
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
        start = t.find("{", start + 1)
    return {}


def _blackboard(n: int = 6) -> str:
    acts = governor.recent_activity(n)
    if not acts:
        return "(no prior activity)"
    return "\n".join(f"- [{a.get('agent','?')}] {a.get('summary','')}" for a in acts)


def run_agent(agent: dict) -> dict:
    name = agent["name"]
    allowed = agent["tools"]
    catalog = "\n".join(f"- {t}: {tools.REGISTRY[t]['desc']}" for t in allowed
                        if t in tools.REGISTRY)
    state = {"brain": tools.t_brain_state(), "ml": tools.t_ml_card(),
             "backtest": tools.t_latest_backtest()}
    mem = memory.recall(agent["mandate"], k=2)
    try:
        from .. import awareness as _aw
        _brief = _aw.brief(8)
    except Exception:  # noqa: BLE001
        _brief = ""
    prompt = (_SYS.format(name=name, mandate=agent["mandate"], catalog=catalog) +
              f"\n\nSYSTEM STATE:\n{json.dumps(state)[:900]}"
              f"\n\nDESK BLACKBOARD (other agents):\n{_blackboard()}"
              f"\n\nDESK MESH (prediction/brain/ml/council all speak here):\n{_brief}"
              f"\n\nYOUR MEMORY:\n" + "\n".join(f"- {m['text'][:90]}" for m in mem))
    raw = _complete(agent["provider"], prompt, agent.get("model"))
    act = _parse_action(raw)
    thought = act.get("thought", raw[:160])
    governor.log_observation(name, thought, {"provider": agent["provider"]})

    tool = act.get("tool")
    if tool not in allowed:
        # keep every agent contributing: default to its primary (read-only) tool
        # rather than sitting the turn out, so the whole mesh stays live.
        tool = allowed[0] if allowed else None
        if tool is None:
            return {"agent": name, "thought": thought, "tool": None, "note": "no tools"}
        thought = (thought + f" [defaulted to {tool}]")[:200]
    args = act.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    # human-in-the-loop: sensitive tools pause for approval instead of executing
    spec = tools.REGISTRY.get(tool, {})
    if spec.get("needs_approval"):
        try:
            from . import state
            aid = state.create_approval(name, tool, {"args": args, "thought": thought})
        except Exception:  # noqa: BLE001
            aid = None
        governor._log({"kind": "proposal", "agent": name, "tool": tool,
                       "summary": f"awaiting human approval: {tool} {str(args)[:80]}"})
        return {"agent": name, "thought": thought, "tool": tool, "args": args,
                "status": "pending_approval", "approval_id": aid}
    result = tools.call(tool, agent=name, **args)
    governor.record_action(name, tool, f"{thought} | {tool} -> {str(result)[:120]}",
                           {"args": args})
    return {"agent": name, "thought": thought, "tool": tool, "args": args,
            "result": result, "status": "done"}


def run_round() -> list[dict]:
    out = []
    for agent in ROSTER:
        try:
            out.append(run_agent(agent))
        except Exception as e:  # noqa: BLE001
            out.append({"agent": agent["name"], "error": str(e)[:140]})
    return out


def main():
    loop = "--loop" in sys.argv
    every = 900
    for i, a in enumerate(sys.argv):
        if a == "--every" and i + 1 < len(sys.argv):
            every = int(sys.argv[i + 1])
    print(f"[agents] roster={[a['name'] for a in ROSTER]} loop={loop} every={every}s")
    while True:
        res = run_round()
  