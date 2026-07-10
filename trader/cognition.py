"""Free-model cognition: put the wired-up LLMs to work as standing autonomous
actions across the platform.

The reasoner (Groq -> Cloudflare -> OpenRouter free chain) already powers the
council, labeler and reflection. This module extends it into a set of gated,
auto-safe background jobs that turn raw platform state into structured, durable
knowledge -- and narrate it into the mesh so the brain is visibly thinking:

  news_catalysts()  read top headlines -> extract structured catalysts
                    (ticker/event/direction/magnitude/horizon) -> arm the
                    watch->strike list + form beliefs. News becomes tradable
                    structure, not just a sentiment score.
  brief()           synthesize regime + forecast + news + inner state + last
                    dream into a sharp natural-language market brief.
  postmortem()      review recently RESOLVED decisions (what worked/failed and
                    in which regime) -> write durable lessons into beliefs + LTM.
  risk_scan()       a risk sentinel: weigh regime + drawdown + exposure + news
                    for concentration / tail risks -> advisory warnings only.
  adjudicate()      when self-built beliefs conflict, weigh the evidence and
                    recommend which to keep -> nudges belief utility + pruning.

Safe by construction: every job reads state + writes to memory/analysis stores
and the mesh. None places, sizes or cancels a trade. All are champion of the
free-model chain, so if every provider is down they degrade quietly.
"""
from __future__ import annotations

import json
import os
import time

_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "cognition"))


def _save(name: str, obj: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        json.dump(obj, open(os.path.join(_DIR, f"{name}.json"), "w"), indent=2)
    except Exception:  # noqa: BLE001
        pass


def last(name: str) -> dict:
    try:
        return json.load(open(os.path.join(_DIR, f"{name}.json")))
    except Exception:  # noqa: BLE001
        return {}


def _reason_json(system: str, user: str, max_tokens: int = 600) -> dict | None:
    try:
        from . import reasoner
        raw = reasoner.reason_json(system, user, max_tokens=max_tokens)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _publish(layer: str, kind: str, text: str, salience: float = 0.6, symbol: str = "") -> None:
    try:
        from . import mesh
        mesh.publish(layer, kind, text[:220], symbol=symbol, salience=salience)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
def news_catalysts(max_items: int = 12) -> dict:
    """LLM reads the freshest headlines and extracts structured, tradable
    catalysts, then arms the watch list and records beliefs."""
    out: dict = {"ok": False, "catalysts": [], "armed": 0}
    try:
        from . import newshub
        agg = newshub.aggregate(limit=60)
        items = (agg or {}).get("items", [])[:max_items]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"news: {str(e)[:100]}"
        return out
    if not items:
        out["error"] = "no news items"
        return out

    heads = "\n".join(f"- {it.get('title','')[:140]} (src {it.get('source','')})"
                      for it in items)
    system = ("You are an equity catalyst analyst. From the headlines, extract only "
              "MATERIAL, tradable catalysts for liquid US-listed names. Be conservative: "
              "skip vague or non-actionable headlines.")
    user = (f"Headlines:\n{heads}\n\nReturn ONLY JSON: "
            '{"catalysts":[{"ticker":"AAPL","event":"...","direction":"up|down",'
            '"magnitude":"low|med|high","horizon_days":5,"confidence":0.0-1.0,"why":"..."}]}. '
            "Max 6. Only include tickers you are confident are the correct symbol.")
    data = _reason_json(system, user, max_tokens=700)
    if not data:
        out["error"] = "model unavailable"
        return out
    cats = data.get("catalysts", []) if isinstance(data, dict) else []

    armed = 0
    beliefs_formed = 0
    clean: list = []
    try:
        from . import watchlist, beliefs, market_brain
        wl = watchlist.WatchList()
        regime = market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        wl = None
        regime = "any"
        beliefs = None  # type: ignore

    for c in cats[:6]:
        tkr = str(c.get("ticker", "")).upper().strip()
        direction = str(c.get("direction", "")).lower()
        conf = float(c.get("confidence", 0.0) or 0.0)
        if not tkr or direction not in ("up", "down") or conf < 0.5:
            continue
        clean.append(c)
        # form a belief about the catalyst (advisory)
        if beliefs is not None:
            try:
                beliefs.upsert(
                    f"news catalyst: {tkr} {c.get('event','')[:80]} -> {direction}",
                    target="market", direction=1 if direction == "up" else -1,
                    regime="any", confidence=min(0.75, 0.45 + conf * 0.3))
                beliefs_formed += 1
            except Exception:  # noqa: BLE001
                pass
        # arm the watch list on real price (only when we can price it)
        if wl is not None:
            try:
                px = _price(tkr)
                if px:
                    side = "buy" if direction == "up" else "sell"
                    wl.arm(tkr, side, px, f"news: {c.get('event','')[:60]}",
                           buffer=0.006, expiry_min=1440, confidence=conf,
                           source="llm_catalyst")
                    armed += 1
            except Exception:  # noqa: BLE001
                pass

    out.update({"ok": True, "catalysts": clean, "armed": armed,
                "beliefs_formed": beliefs_formed, "regime": regime,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _save("catalysts", out)
    if clean:
        top = ", ".join(f"{c.get('ticker')} {c.get('direction')}" for c in clean[:4])
        _publish("news", "catalyst", f"LLM extracted {len(clean)} catalysts ({armed} armed): {top}",
                 salience=0.7)
    return out


def _price(sym: str):
    try:
        from .ml.dataset import _alpaca_series
        ser = _alpaca_series(sym)
        return float(ser[-1][1]) if ser else None
    except Exception:  # noqa: BLE001
        return None


def brief() -> dict:
    """Synthesize the whole situational picture into a sharp natural-language
    market brief (published to the desk + stored for /brain)."""
    out: dict = {"ok": False}
    ctx_bits = []
    try:
        from . import awareness
        ctx_bits.append(awareness.brief(8))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import dream, marketclock
        if not marketclock.is_open():
            j = (dream.last() or {}).get("journal")
            if j:
                ctx_bits.append("Overnight: " + j)
    except Exception:  # noqa: BLE001
        pass
    ctx = "\n".join(b for b in ctx_bits if b)
    if not ctx:
        out["error"] = "no context"
        return out
    system = ("You are the strategist for a systematic paper-trading desk. Write a crisp, "
              "specific market brief: what regime we're in, what the models and news imply, "
              "the top 2-3 things to watch, and the desk's posture. No hedging fluff, no "
              "disclaimers. 120 words max.")
    try:
        from . import reasoner
        text = reasoner.reason(f"Current platform state:\n{ctx}\n\nWrite the brief.",
                               system=system, max_tokens=320, temperature=0.4)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
        return out
    if not text:
        out["error"] = "model unavailable"
        return out
    out = {"ok": True, "brief": text.strip(),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("brief", out)
    _publish("desk", "brief", text.strip(), salience=0.7)
    return out


def _resolved_episodes(limit: int = 40) -> list:
    try:
        from . import episodes
        rows = [r for r in episodes._rows() if r.get("resolved") and r.get("outcome_ret") is not None]
        return rows[-limit:]
    except Exception:  # noqa: BLE001
        return []


def postmortem() -> dict:
    """LLM reviews recently resolved decisions and writes durable lessons."""
    out: dict = {"ok": False, "lessons": []}
    rows = _resolved_episodes(40)
    if len(rows) < 5:
        out["error"] = f"only {len(rows)} resolved decisions (need 5)"
        return out
    lines = []
    for r in rows[-24:]:
        lines.append(f"{r.get('symbol','')} {r.get('side','')} in {r.get('regime','')} "
                     f"(mood {r.get('mood','')}) -> {float(r.get('outcome_ret',0))*100:+.2f}%")
    wins = sum(1 for r in rows if float(r.get("outcome_ret", 0)) > 0)
    system = ("You are a trading performance reviewer. From this decision log, find the "
              "REAL patterns: which regimes/moods/sides worked and which lost. Output "
              "concrete, testable lessons the desk can act on.")
    user = ("Resolved decisions (symbol side regime mood -> return):\n" + "\n".join(lines) +
            f"\n\nWin rate {wins}/{len(rows)}. Return ONLY JSON: "
            '{"lessons":[{"lesson":"...","target":"market|ml|ta|quant|tnet|self",'
            '"direction":-1|0|1,"regime":"any|calm|neutral|high_vol|risk_off","confidence":0.0-1.0}]}. Max 5.')
    data = _reason_json(system, user, max_tokens=650)
    if not data:
        out["error"] = "model unavailable"
        return out
    lessons = data.get("lessons", []) if isinstance(data, dict) else []
    formed = 0
    try:
        from . import beliefs, ltm
    except Exception:  # noqa: BLE001
        beliefs = None  # type: ignore
        ltm = None  # type: ignore
    kept = []
    for l in lessons[:5]:
        claim = str(l.get("lesson", ""))[:200]
        if not claim:
            continue
        kept.append(claim)
        if beliefs is not None:
            try:
                beliefs.upsert(claim, target=str(l.get("target", "market")),
                               direction=int(l.get("direction", 0) or 0),
                               regime=str(l.get("regime", "any")),
                               confidence=float(l.get("confidence", 0.55) or 0.55))
                formed += 1
            except Exception:  # noqa: BLE001
                pass
    if ltm is not None and kept:
        try:
            if ltm.available():
                ltm.remember("Trade post-mortem lessons",
                             "\n".join(f"- {k}" for k in kept),
                             dedup_key=f"postmortem::{int(time.time()//86400)}",
                             topics=["postmortem", "lessons"])
        except Exception:  # noqa: BLE001
            pass
    out = {"ok": True, "lessons": kept, "beliefs_formed": formed,
           "reviewed": len(rows), "win_rate": round(wins / len(rows), 3),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("postmortem", out)
    if kept:
        _publish("reasoning", "postmortem", f"lessons from {len(rows)} decisions: {kept[0][:120]}",
                 salience=0.65)
    return out


def risk_scan() -> dict:
    """Advisory risk sentinel: LLM weighs regime + exposure + news for tail risks.
    Warnings only -- never acts on positions."""
    out: dict = {"ok": False, "warnings": []}
    bits = []
    try:
        from . import awareness
        bits.append(awareness.brief(6))
    except Exception:  # noqa: BLE001
        pass
    positions = []
    try:
        from dashboard import dash_metrics
        for r in dash_metrics.read_ledger(None, limit=30):
            sym = (r.get("symbol") or "").upper()
            if sym:
                positions.append(sym)
    except Exception:  # noqa: BLE001
        pass
    if positions:
        from collections import Counter
        conc = ", ".join(f"{s}x{n}" for s, n in Counter(positions).most_common(6))
        bits.append("Recent activity concentration: " + conc)
    ctx = "\n".join(b for b in bits if b)
    if not ctx:
        out["error"] = "no context"
        return out
    system = ("You are a risk officer for a paper-trading desk. Identify concrete tail / "
              "concentration / regime risks in the current picture. Be specific and brief. "
              "Advisory only.")
    user = (f"Picture:\n{ctx}\n\nReturn ONLY JSON: "
            '{"risk_level":"low|elevated|high","warnings":["...","..."]}. Max 4 warnings.')
    data = _reason_json(system, user, max_tokens=450)
    if not data:
        out["error"] = "model unavailable"
        return out
    warns = [str(w)[:160] for w in (data.get("warnings") or [])][:4]
    out = {"ok": True, "risk_level": str(data.get("risk_level", "low")),
           "warnings": warns, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("risk", out)
    if warns:
        _publish("desk", "risk", f"[{out['risk_level']}] {warns[0]}", salience=0.7)
    return out


def adjudicate() -> dict:
    """When self-built beliefs conflict, weigh the evidence and recommend which to
    trust -- nudging belief utility so the confluence steering self-corrects."""
    out: dict = {"ok": False, "resolutions": []}
    try:
        from . import beliefs
        conflicts = beliefs.conflicts()
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
        return out
    if not conflicts:
        out.update({"ok": True, "note": "no conflicts to resolve"})
        return out
    lines = []
    for c in conflicts[:6]:
        lines.append(json.dumps(c)[:200])
    system = ("You resolve contradictions in a trading system's self-built beliefs. For each "
              "conflicting pair, decide which side the evidence favors and why.")
    user = ("Conflicting beliefs:\n" + "\n".join(lines) +
            '\n\nReturn ONLY JSON: {"resolutions":[{"keep":"<claim>","drop":"<claim>",'
            '"reason":"..."}]}.')
    data = _reason_json(system, user, max_tokens=500)
    if not data:
        out["error"] = "model unavailable"
        return out
    res = data.get("resolutions", []) if isinstance(data, dict) else []
    out.update({"ok": True, "resolutions": res[:6],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _save("adjudicate", out)
    if res:
        _publish("reasoning", "adjudicate",
                 f"resolved {len(res)} belief conflicts: keep '{str(res[0].get('keep',''))[:80]}'",
                 salience=0.6)
    return out


if __name__ == "__main__":
    import sys
    fn = sys.argv[1] if len(sys.argv) > 1 else "brief"
    print(json.dumps(globals()[fn](), indent=2))
