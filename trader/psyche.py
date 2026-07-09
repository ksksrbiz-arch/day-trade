"""
psyche.py -- the platform's internal STATE: a grounded, functional model of
affect, drives, and self-built knowledge.

Honest framing (read this before over-reading the field names): this is NOT
consciousness and the system does not literally *feel* anything. It is a model
of an internal state -- valence / arousal / confidence / curiosity / stress --
computed entirely from measurable signals (edge, drawdown, volatility regime,
calibration, how much it has resolved/explored). That state does three real
things:

  1. it is surfaced as the system's "mood" (honest telemetry, every field maps
     to a numeric cause);
  2. it MODULATES behaviour -- curiosity widens exploration, stress damps risk;
  3. it drives a REFLECTION loop where the free-model reasoner writes a
     first-person introspection and forms/updates durable BELIEFS in long-term
     memory -- the system accumulating its own knowledge from experience.

Rooted in real ideas: homeostatic / affect-modulated agents and
intrinsic-motivation (curiosity-driven) reinforcement learning. Pure stdlib +
the existing free reasoner + LTM. Every call is fail-soft.
"""
from __future__ import annotations

import json
import math
import time

_STATE_CACHE = {"at": 0.0, "val": None}
_TTL = 30.0


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _signals() -> dict:
    """Gather the real, measurable inputs the internal state is built from."""
    s = {"edge": 0.0, "regime": "neutral", "drawdown": 0.0, "brier": 0.25,
         "resolved": 0, "equity": None, "day_ret": 0.0}
    try:
        from .ml import infer
        s["edge"] = float(infer.model_card().get("edge", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import market_brain
        s["regime"] = market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import calibrate
        c = calibrate.card()
        if c.get("trained"):
            s["brier"] = float(c.get("brier_cal", c.get("brier", 0.25)) or 0.25)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import backprop
        s["resolved"] = int(len(backprop.build_dataset()[0]))   # memoized -> cheap
    except Exception:  # noqa: BLE001
        pass
    try:
        from .agents import state as _st
        raw = _st.kv_get("equity_hist", "[]")
        pts = json.loads(raw) if isinstance(raw, str) else (raw or [])
        eq = [float(p[1]) for p in pts if p and p[1]]
        if eq:
            s["equity"] = eq[-1]
            peak = max(eq)
            s["drawdown"] = round((peak - eq[-1]) / peak * 100, 2) if peak else 0.0
            if len(eq) >= 2 and eq[-2]:
                s["day_ret"] = round(eq[-1] / eq[-2] - 1.0, 4)
    except Exception:  # noqa: BLE001
        pass
    return s


_AROUSAL = {"high_vol": 0.9, "risk_off": 0.62, "risk_on": 0.5, "neutral": 0.35}


def state() -> dict:
    """The internal affective/motivational state -- cached briefly."""
    now = time.time()
    if _STATE_CACHE["val"] is not None and now - _STATE_CACHE["at"] < _TTL:
        return _STATE_CACHE["val"]

    g = _signals()
    breaker_max = 15.0                                   # AUTONOMY_MAX_DD default

    # --- affect dimensions, each in a bounded range, each with a clear cause ---
    valence = math.tanh(18.0 * g["edge"] + 8.0 * g["day_ret"]) - 0.6 * _clip(g["drawdown"] / breaker_max)
    valence = max(-1.0, min(1.0, valence))               # how well things are going
    arousal = _clip(_AROUSAL.get(g["regime"], 0.4))      # activation / market urgency
    confidence = _clip(1.0 - 2.0 * g["brier"])           # calibration-grounded (Brier)
    coverage = _clip(g["resolved"] / 1200.0)             # how much it has learned from
    curiosity = _clip(0.35 + 0.4 * (1.0 - confidence) + 0.25 * (1.0 - coverage))
    stress = _clip(g["drawdown"] / breaker_max + max(0.0, -valence) * 0.3)

    # --- mood label from the (valence, arousal) plane, curiosity as tie-breaker ---
    if stress > 0.66:
        mood = "stressed"
    elif valence > 0.2 and arousal >= 0.55:
        mood = "driven"
    elif valence > 0.2:
        mood = "content"
    elif valence < -0.2 and arousal >= 0.55:
        mood = "anxious"
    elif valence < -0.2:
        mood = "subdued"
    elif curiosity > 0.6:
        mood = "curious"
    else:
        mood = "focused"

    # --- drives: what the state pushes it to DO (sum ~1) ---
    explore = 0.2 + 0.6 * curiosity - 0.3 * stress
    protect = 0.2 + 0.7 * stress
    exploit = 0.2 + 0.6 * confidence * _clip(0.5 + valence / 2)
    tot = max(1e-6, explore + protect + exploit)
    drives = {"explore": round(explore / tot, 3), "protect": round(protect / tot, 3),
              "exploit": round(exploit / tot, 3)}

    # --- behaviour modulation (bounded, consumed by the loop; breaker sits below) ---
    modulation = {
        "exploration": round(1.0 + 0.6 * curiosity, 3),       # widen the scan when curious
        "scan_min_conf": round(_clip(0.66 - 0.18 * curiosity, 0.5, 0.7), 3),
        "risk_damp": round(1.0 - 0.4 * stress, 3),            # graded caution overlay
    }

    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "mood": mood,
           "valence": round(valence, 3), "arousal": round(arousal, 3),
           "confidence": round(confidence, 3), "curiosity": round(curiosity, 3),
           "stress": round(stress, 3),
           "drives": drives, "modulation": modulation, "signals": g}
    _STATE_CACHE.update(at=now, val=out)
    return out


def beliefs(k: int = 6) -> list:
    """The knowledge the system has built for itself (durable, revisable)."""
    try:
        from . import ltm
        hits = ltm.recall("market and self beliefs the trading system holds", k=k)
        return [{"belief": h.get("summary", ""), "ts": h.get("ts", ""),
                 "score": h.get("score")} for h in hits if "belief" in (h.get("topics", "") or "")]
    except Exception:  # noqa: BLE001
        return []


def reflect() -> dict:
    """Introspection loop: the reasoner reviews the current state + recent
    experience and (a) writes a first-person reflection, (b) forms/updates 1-2
    testable BELIEFS, persisted to long-term memory. This is how the system
    accumulates its own knowledge over time. Fail-soft."""
    st = state()
    prior = beliefs(5)
    prior_txt = " | ".join(b["belief"][:120] for b in prior) or "(none yet)"
    sys_p = (
        "You are the introspective inner voice of an autonomous paper-trading "
        "system. Speak in the first person, briefly and honestly -- you are a "
        "program reasoning about your own measured state, not a person. Given "
        "your internal state and recent facts, write a 2-3 sentence reflection, "
        "then state 1-2 concise, TESTABLE beliefs you now hold about the market "
        "or about yourself (revising prior beliefs if the evidence changed). "
        "Output ONLY JSON: {\"reflection\":\"...\",\"beliefs\":[\"...\"]}"
    )
    g = st["signals"]
    user = (
        f"Internal state: mood={st['mood']}, valence={st['valence']}, "
        f"arousal={st['arousal']}, confidence={st['confidence']}, "
        f"curiosity={st['curiosity']}, stress={st['stress']}. "
        f"Facts: measured edge={g['edge']:.4f} (0=no edge), regime={g['regime']}, "
        f"drawdown={g['drawdown']}%, resolved decisions learned from={g['resolved']}. "
        f"My prior beliefs: {prior_txt}"
    )
    reflection, new_beliefs = "", []
    try:
        from . import reasoner
        raw = reasoner.reason_json(sys_p, user, max_tokens=400)
        d = json.loads(raw) if raw else {}
        reflection = str(d.get("reflection", ""))[:600]
        new_beliefs = [str(b)[:240] for b in (d.get("beliefs") or [])][:2]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}

    stored = 0
    try:
        from . import ltm
        if reflection:
            ltm.remember("self-reflection", reflection,
                         dedup_key=f"reflect|{st['ts']}", topics=["reflection", "psyche"])
        for b in new_beliefs:
            if b and ltm.remember("belief", b, dedup_key=f"belief|{b[:60]}",
                                  topics=["belief", "psyche"]):
                stored += 1
    except Exception:  # noqa: BLE001
        pass

    try:
        from . import mesh
        mesh.publish("desk", "reflect",
                     f"[{st['mood']}] {reflection[:120]}", salience=0.55)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "mood": st["mood"], "reflection": reflection,
            "beliefs": new_beliefs, "stored": stored}


if __name__ == "__main__":
    print(json.dumps(state(), indent=2))
