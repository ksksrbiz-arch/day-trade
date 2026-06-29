"""Tool registry the autonomous agents can call. Read/analysis tools are free;
mutating tools route through the governor. Each tool returns a JSON-able dict.
"""
from __future__ import annotations

import json
import os

from . import governor, memory, cloudflare as cf
from . import actions

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.abspath(os.path.join(_HERE, "..", ".."))


# ----------------------- read / analysis tools ------------------------------ #
def t_brain_state(**_):
    try:
        from .. import market_brain
        return {"regime": market_brain.cached_regime("neutral"),
                "posture": market_brain.cached_posture("equity"),
                "crypto_posture": market_brain.cached_posture("crypto")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_ml_card(**_):
    try:
        from ..ml.infer import model_card
        c = model_card()
        return {k: c.get(k) for k in ("trained", "auc", "acc", "edge", "trade_samples")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_confluence(symbol: str = "AAPL", **_):
    try:
        from .. import alpha
        from ..crsp import query as crsp
        bars = crsp.get_prices(symbol.upper(), "2024-06-01", None)
        closes = [b["close"] for b in bars if b.get("close")]
        conv = alpha.analyze(closes, symbol=symbol.upper())
        return {"symbol": symbol.upper(), "composite": conv.composite,
                "side": conv.side, "pass": conv.gate_pass, "scores": conv.scores}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_latest_backtest(**_):
    p = os.path.join(_PROJ, "data", "backtests", "latest.json")
    if not os.path.exists(p):
        return {"empty": True}
    try:
        d = json.loads(open(p).read())
        return {"oos": d.get("oos"), "spy": d.get("spy"),
                "edge_vs_spy_pct": d.get("edge_vs_spy_pct"), "meta": d.get("meta")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_news_sentiment(symbol: str = "SPY", **_):
    """Cloudflare sentiment over recent per-ticker headlines."""
    try:
        from .. import newsfeeds
        items = newsfeeds.ticker_news(symbol.upper())[:6]
        if not items:
            return {"symbol": symbol.upper(), "n": 0, "sentiment": 0.0}
        scores = [cf.sentiment(i.get("title", "")) for i in items]
        avg = round(sum(scores) / len(scores), 3) if scores else 0.0
        return {"symbol": symbol.upper(), "n": len(scores), "sentiment": avg,
                "headlines": [i.get("title", "")[:80] for i in items[:3]]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_wsb_buzz(symbol: str = "", **_):
    """WallStreetBets social buzz; if symbol given, cross-reference it."""
    try:
        from .. import wsb
        if symbol:
            return wsb.cross_reference(symbol)
        return wsb.buzz()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_recall(query: str = "", **_):
    return {"memories": memory.recall(query or "trading regime risk", k=3)}


# ----------------------- mutating / governed tools -------------------------- #
def t_run_backtest(agent: str = "system", days: int = 900, pit_asof: str = None, **_):
    try:
        from .. import walkforward
        res = walkforward.run(days=int(days), pit_asof=pit_asof, out="agent_latest.json")
        summ = {"edge_vs_spy_pct": res.get("edge_vs_spy_pct"),
                "oos_total": res.get("oos", {}).get("total"),
                "spy_total": res.get("spy", {}).get("total")}
        governor.record_action(agent, "run_backtest",
                               f"backtest edge {summ['edge_vs_spy_pct']}% vs SPY", summ)
        return summ
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


def t_retrain_ml(agent: str = "system", **_):
    try:
        from ..ml.train import train_once
        r = train_once()
        governor.record_action(agent, "retrain_ml",
                               f"retrain AUC {r.get('auc')} promoted={r.get('promoted')}", r)
        return r
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


def t_reconcile_agents(agent: str = "system", **_):
    try:
        from ..ml.agent_reliability import reconcile
        r = reconcile()
        governor.record_action(agent, "reconcile_agents",
                               f"reconciled {r.get('reconciled')} council records", r)
        return r
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


def t_propose_param(agent: str = "system", name: str = "", value=None, rationale: str = "", **_):
    return governor.propose_param(agent, name, value, rationale)


# ----------------- self-tuning: ensemble audit + voice controls ------------- #
def t_edge_report(**_):
    """Forward-edge report: which signal sources have proven edge vs coin/SPY."""
    try:
        from .. import edge
        r = edge.report()
        return {"summary": r["summary"], "baseline": r.get("baseline"),
                "sources": [{"source": s["source"], "verdict": s["verdict"],
                             "hit_rate": s.get("hit_rate"), "resolved": s.get("resolved")}
                            for s in r.get("sources", [])]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_attribution(**_):
    """Voice P&L attribution: which confluence voice actually makes money."""
    try:
        from .. import attribution
        r = attribution.report()
        return {"summary": r["summary"], "resolved": r.get("resolved"),
                "voices": [{"voice": v["voice"], "attributed_return_pct": v["attributed_return_pct"],
                            "verdict": v["verdict"], "lead_decisions": v.get("lead_decisions")}
                           for v in r.get("voices", [])]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def t_voices_state(**_):
    """Current confluence voice weights + mute/pin overrides."""
    try:
        from .. import voices
        s = voices.summary()
        return {"weights_source": s["weights_source"],
                "voices": [{"voice": v["voice"], "effective": v["effective"],
                            "muted": v["muted"], "pinned": v["pinned"],
                            "attributed_return_pct": v.get("attributed_return_pct"),
                            "verdict": v.get("verdict")} for v in s["voices"]]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def _attr_verdict(voice: str):
    try:
        from .. import attribution
        for v in attribution.report().get("voices", []):
            if v["voice"] == voice:
                return v
    except Exception:  # noqa: BLE001
        pass
    return None


def t_mute_voice(agent: str = "system", voice: str = "", on: bool = True, **_):
    """Mute/unmute a confluence voice. GUARD: muting requires the voice to be
    proven unprofitable on resolved attribution; unmute is always allowed."""
    from .. import voices
    if voice not in voices.METHODS:
        return {"error": f"unknown voice {voice}"}
    if not on:
        voices.set_mute(voice, False)
        governor.record_action(agent, "mute_voice", f"unmuted {voice}", {})
        return {"voice": voice, "muted": False}
    v = _attr_verdict(voice)
    if not v or str(v.get("verdict", "")).startswith("maturing"):
        return {"refused": True, "reason": f"{voice} attribution still maturing — "
                "insufficient resolved decisions to justify muting"}
    if v.get("verdict") != "unprofitable":
        return {"refused": True, "reason": f"{voice} is not proven unprofitable "
                f"(verdict={v.get('verdict')})"}
    voices.set_mute(voice, True)
    governor.record_action(agent, "mute_voice",
                           f"muted {voice} (attributed {v.get('attributed_return_pct')}%)", v)
    return {"voice": voice, "muted": True, "evidence": v}


def t_pin_voice(agent: str = "system", voice: str = "", weight=None, **_):
    """Pin/unpin a voice's confluence weight. GUARD: pinning requires the voice
    to be proven profitable on resolved attribution; unpin is always allowed."""
    from .. import voices
    if voice not in voices.METHODS:
        return {"error": f"unknown voice {voice}"}
    if weight is None:
        voices.set_pin(voice, None)
        governor.record_action(agent, "pin_voice", f"unpinned {voice}", {})
        return {"voice": voice, "pinned": None}
    v = _attr_verdict(voice)
    if not v or v.get("verdict") != "profitable":
        return {"refused": True, "reason": f"{voice} not proven profitable yet "
                f"(verdict={v.get('verdict') if v else 'n/a'})"}
    voices.set_pin(voice, float(weight))
    governor.record_action(agent, "pin_voice", f"pinned {voice} at {weight}", v)
    return {"voice": voice, "pinned": float(weight), "evidence": v}


REGISTRY = {
    "brain_state":     {"fn": t_brain_state, "mutating": False,
                        "desc": "current cross-asset regime + posture"},
    "ml_card":         {"fn": t_ml_card, "mutating": False,
                        "desc": "ML model validation AUC/edge/sample counts"},
    "confluence":      {"fn": t_confluence, "mutating": False,
                        "desc": "multi-method conviction for a symbol (arg: symbol)"},
    "latest_backtest": {"fn": t_latest_backtest, "mutating": False,
                        "desc": "most recent walk-forward result vs SPY"},
    "news_sentiment":  {"fn": t_news_sentiment, "mutating": False,
                        "desc": "Cloudflare sentiment over recent headlines (arg: symbol)"},
    "wsb_buzz":        {"fn": t_wsb_buzz, "mutating": False,
                        "desc": "WallStreetBets social buzz / cross-reference a ticker (arg: symbol)"},
    "recall":          {"fn": t_recall, "mutating": False,
                        "desc": "recall similar past situations (arg: query)"},
    "run_backtest":    {"fn": t_run_backtest, "mutating": True,
                        "desc": "run a walk-forward backtest (args: days, pit_asof)"},
    "retrain_ml":      {"fn": t_retrain_ml, "mutating": True,
                        "desc": "retrain the ML model (champion/challenger gated)"},
    "reconcile_agents": {"fn": t_reconcile_agents, "mutating": True,
                        "desc": "score council agents vs realized outcomes"},
    "propose_param":   {"fn": t_propose_param, "mutating": True,
                        "desc": "propose a bounded change to the operating scheme "
                                "(args: name, value, rationale)"},
}



# heavier action tools (files / code / subagents / offload)
REGISTRY.update({
    "file_read":       {"fn": actions.file_read, "mutating": False, "needs_approval": False,
                        "desc": "read a project file (arg: path, relative to project root)"},
    "file_write":      {"fn": actions.file_write, "mutating": True, "needs_approval": True,
                        "desc": "write a file in the agent workspace sandbox (args: path, content)"},
    "run_python":      {"fn": actions.run_python, "mutating": True, "needs_approval": True,
                        "desc": "execute a Python snippet in a sandboxed subprocess (arg: code)"},
    "summarize_offload": {"fn": actions.summarize_offload, "mutating": False, "needs_approval": False,
                        "desc": "summarize + offload a large blob to keep context small (args: text,label)"},
    "spawn_subagents": {"fn": actions.t_spawn_subagents, "mutating": False, "needs_approval": False,
                        "desc": "run focused subagents in parallel isolated contexts (arg: tasks=[{role,task}])"},
})
# self-tuning ensemble audit + voice controls
REGISTRY.update({
    "edge_report":   {"fn": t_edge_report, "mutating": False, "needs_approval": False,
                      "desc": "forward-edge report: which sources have proven edge vs coin/SPY"},
    "attribution":   {"fn": t_attribution, "mutating": False, "needs_approval": False,
                      "desc": "voice P&L attribution: which confluence voice actually makes money"},
    "voices_state":  {"fn": t_voices_state, "mutating": False, "needs_approval": False,
                      "desc": "current confluence voice weights + mute/pin state"},
    "mute_voice":    {"fn": t_mute_voice, "mutating": True, "needs_approval": True,
                      "desc": "mute/unmute a confluence voice (args: voice, on) — guarded: "
                              "muting requires proven-unprofitable resolved attribution"},
    "pin_voice":     {"fn": t_pin_voice, "mutating": True, "needs_approval": True,
                      "desc": "pin/unpin a voice weight (args: voice, weight) — guarded: "
                              "pinning requires proven-profitable resolved attribution"},
})
for _spec in REGISTRY.values():
    _spec.setdefault("needs_approval", False)

def call(tool_name: str, agent: str = "system", **kwargs) -> dict:
    # NB: first param is `tool_name` (not `name`) so a tool arg called `name`
    # (e.g. propose_param's param name) can't collide with this signature.
    spec = REGISTRY.get(tool_name)
    if not spec:
        return {"error": f"unknown tool {tool_name}"}
    try:
        if spec["mutating"]:
            kwargs.setdefault("agent", agent)
        return spec["fn"](**kwargs)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{tool_name} failed: {str(e)[:120]}"}


def catalog() -> str:
    return "\n".join(f"- {k}: {v['desc']}" for k, v in REGISTRY.items())


if __name__ == "__main__":
    print(catalog())
    print("brain:", t_brain_state())
    print("ml:", t_ml_card())
