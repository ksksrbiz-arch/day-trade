"""
psyche.py -- the platform's INTERNAL STATE + self-knowledge loop.

Honest framing: NOT consciousness, and it does not literally feel. It is a
grounded model of an internal state (valence/arousal/confidence/curiosity/stress
-> mood + drives), computed from measured signals, that (1) is surfaced as the
system's "mood", (2) MODULATES behaviour (curiosity widens exploration; stress
damps risk), and (3) drives a REFLECTION loop that forms/updates durable,
structured BELIEFS (trader/beliefs.py) which feed back into strategy weighting,
plus SECOND-ORDER beliefs about its own behaviour learned from EPISODIC memory
(trader/episodes.py). Mood uses hysteresis (slow to improve, fast to deteriorate)
and a recovery mode. Rooted in homeostatic/affect-modulated agents +
intrinsic-motivation RL. Pure stdlib + free reasoner + LTM; fail-soft.
"""
from __future__ import annotations

import json
import math
import time

_STATE_CACHE = {"at": 0.0, "val": None}
_TTL = 30.0
_AROUSAL = {"high_vol": 0.9, "risk_off": 0.62, "risk_on": 0.5, "neutral": 0.35}
_VOICE_HINT = "ta, quant, fundamental, ml, council, prediction, tnet, alpha_engine, cortex, factors"


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _kv():
    from .agents import state as _st
    return _st


def _signals() -> dict:
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
        s["resolved"] = int(len(backprop.build_dataset()[0]))
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = _kv().kv_get("equity_hist", "[]")
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


def state() -> dict:
    now = time.time()
    if _STATE_CACHE["val"] is not None and now - _STATE_CACHE["at"] < _TTL:
        return _STATE_CACHE["val"]

    g = _signals()
    breaker_max = 15.0

    raw_valence = math.tanh(18.0 * g["edge"] + 8.0 * g["day_ret"]) - 0.6 * _clip(g["drawdown"] / breaker_max)
    raw_valence = max(-1.0, min(1.0, raw_valence))

    # --- MOOD HYSTERESIS: slow to improve (needs sustained gains), fast to
    # deteriorate on losses -- matches real performance psychology, prevents thrash.
    try:
        prev_v = float(_kv().kv_get("psyche_valence", raw_valence))
    except Exception:  # noqa: BLE001
        prev_v = raw_valence
    a_up, a_down = 0.30, 0.70
    valence = prev_v + (a_up if raw_valence > prev_v else a_down) * (raw_valence - prev_v)
    valence = max(-1.0, min(1.0, valence))
    try:
        _kv().kv_set("psyche_valence", round(valence, 4))
    except Exception:  # noqa: BLE001
        pass

    arousal = _clip(_AROUSAL.get(g["regime"], 0.4))
    confidence = _clip(1.0 - 2.0 * g["brier"])
    coverage = _clip(g["resolved"] / 1200.0)
    curiosity = _clip(0.35 + 0.4 * (1.0 - confidence) + 0.25 * (1.0 - coverage))
    stress = _clip(g["drawdown"] / breaker_max + max(0.0, -valence) * 0.3)

    # --- RECOVERY MODE: after a real drawdown, suppress risk-seeking curiosity
    # until performance stabilises (calibration + shallow drawdown).
    try:
        recovering = bool(_kv().kv_get("psyche_recovering", False))
    except Exception:  # noqa: BLE001
        recovering = False
    if g["drawdown"] >= 5.0:
        recovering = True
    elif g["drawdown"] <= 2.0 and confidence >= 0.5:
        recovering = False
    try:
        _kv().kv_set("psyche_recovering", recovering)
    except Exception:  # noqa: BLE001
        pass
    if recovering:
        curiosity *= 0.6                       # cautious exploration while healing

    if stress > 0.66:
        mood = "stressed"
    elif recovering:
        mood = "recovering"
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

    explore = 0.2 + 0.6 * curiosity - 0.3 * stress
    protect = 0.2 + 0.7 * stress
    exploit = 0.2 + 0.6 * confidence * _clip(0.5 + valence / 2)
    tot = max(1e-6, explore + protect + exploit)
    drives = {"explore": round(explore / tot, 3), "protect": round(protect / tot, 3),
              "exploit": round(exploit / tot, 3)}

    modulation = {
        "exploration": round(1.0 + 0.6 * curiosity, 3),
        "scan_min_conf": round(_clip(0.66 - 0.18 * curiosity, 0.5, 0.7), 3),
        "risk_damp": round(1.0 - 0.4 * stress, 3),
    }

    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "mood": mood, "recovering": recovering,
           "valence": round(valence, 3), "arousal": round(arousal, 3),
           "confidence": round(confidence, 3), "curiosity": round(curiosity, 3),
           "stress": round(stress, 3),
           "drives": drives, "modulation": modulation, "signals": g}
    _STATE_CACHE.update(at=now, val=out)
    return out


def beliefs(k: int = 8) -> list:
    try:
        from . import beliefs as _b
        return [{"belief": r["claim"], "target": r["target"], "direction": r["direction"],
                 "regime": r["regime"], "confidence": r["eff_confidence"],
                 "utility": r.get("utility", 0.0)} for r in _b.all_beliefs()[:k]]
    except Exception:  # noqa: BLE001
        return []


def evaluate_beliefs() -> int:
    """EVALUATOR: grade each belief by whether trades made while it was active
    beat the baseline outcome, and set its utility accordingly -- so proven
    beliefs get more authority over strategy weighting and untested ones stay
    advisory. Reads episodic memory."""
    try:
        from . import episodes, beliefs as _b
        rows = [r for r in episodes._rows() if r.get("resolved") and r.get("outcome_ret") is not None]
        if len(rows) < 8:
            return 0
        base = sum(r["outcome_ret"] for r in rows) / len(rows)
        updated = 0
        for bel in _b.all_beliefs():
            active = [r["outcome_ret"] for r in rows if bel["id"] in (r.get("active_beliefs") or [])]
            if len(active) < 4:
                continue
            lift = sum(active) / len(active) - base
            _b.set_utility(bel["id"], max(-1.0, min(1.0, lift * 50.0)))   # ~2% lift -> full authority
            updated += 1
        return updated
    except Exception:  # noqa: BLE001
        return 0


def reflect() -> dict:
    """Introspection loop: resolve episodes, review internal state + recent
    experience + behavioural patterns, then (a) write a first-person reflection,
    (b) form/update STRUCTURED market beliefs that feed strategy weighting, and
    (c) form SECOND-ORDER beliefs about the system's own behaviour. If beliefs
    conflict, focus the reflection on resolving the dissonance."""
    try:
        from . import episodes
        episodes.resolve()
    except Exception:  # noqa: BLE001
        pass
    evaluate_beliefs()

    st = state()
    g = st["signals"]
    try:
        from . import beliefs as _b
        conflicts = _b.conflicts()
        behav = None
        from . import episodes as _ep
        behav = _ep.behavior_stats()[:4]
        similar = _ep.recall_similar(g["regime"], st["mood"])
    except Exception:  # noqa: BLE001
        conflicts, behav, similar = [], [], {}

    focus = ""
    if conflicts:
        c = conflicts[0]
        focus = (f" You hold CONFLICTING beliefs about '{c['target']}': "
                 f"\"{c['a']}\" vs \"{c['b']}\". Resolve which the evidence favours.")

    sys_p = (
        "You are the introspective inner voice of an autonomous paper-trading "
        "system -- a program reasoning about your own measured state, honestly and "
        "in the first person. Write a 2-3 sentence reflection, then output beliefs "
        "you now hold. A market belief targets one voice and says to trust it more "
        "(direction 1) or less (-1) in a regime. A self belief is about your own "
        "behaviour. Voices: " + _VOICE_HINT + ". Regimes: risk_on, risk_off, "
        "high_vol, neutral, any. Output ONLY JSON: {\"reflection\":\"...\","
        "\"beliefs\":[{\"claim\":\"...\",\"target\":\"<voice|market|self>\","
        "\"direction\":-1|0|1,\"regime\":\"<regime|any>\"}]}"
    )
    user = (
        f"Internal state: mood={st['mood']}, valence={st['valence']}, "
        f"confidence={st['confidence']}, curiosity={st['curiosity']}, "
        f"stress={st['stress']}, recovering={st['recovering']}. "
        f"Facts: measured edge={g['edge']:.4f} (0=none), regime={g['regime']}, "
        f"drawdown={g['drawdown']}%, decisions learned from={g['resolved']}. "
        f"My behaviour by state (avg return %): {behav}. "
        f"Outcomes last time I was {st['mood']} in {g['regime']}: {similar}.{focus}"
    )

    reflection, structured = "", []
    try:
        from . import reasoner
        raw = reasoner.reason_json(sys_p, user, max_tokens=500)
        d = json.loads(raw) if raw else {}
        reflection = str(d.get("reflection", ""))[:600]
        structured = d.get("beliefs") or []
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}

    stored = 0
    try:
        from . import beliefs as _b, ltm
        for it in structured[:3]:
            claim = str(it.get("claim", ""))[:240]
            if not claim:
                continue
            _b.upsert(claim, str(it.get("target", "market")),
                      int(it.get("direction", 0) or 0), str(it.get("regime", "any")),
                      confidence=0.55)
            ltm.remember("belief", claim, dedup_key=f"belief|{claim[:60]}", topics=["belief", "psyche"])
            stored += 1
        if reflection:
            ltm.remember("self-reflection", reflection, dedup_key=f"reflect|{st['ts']}",
                         topics=["reflection", "psyche"])
    except Exception:  # noqa: BLE001
        pass

    try:
        from . import mesh
        mesh.publish("desk", "reflect", f"[{st['mood']}] {reflection[:120]}", salience=0.55)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "mood": st["mood"], "reflection": reflection,
            "beliefs_formed": stored, "conflicts": len(conflicts)}


if __name__ == "__main__":
    print(json.dumps(state(), indent=2))
