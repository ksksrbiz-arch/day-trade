"""
beliefs.py -- the platform's SELF-BUILT, structured knowledge, and how it feeds
back into trading.

Semantic memory ("what the system knows") stored as versioned records:

    {id, claim, target, direction, regime, confidence, evidence_count,
     utility, created, last_revised}

  * target     : a voice name (ta/quant/ml/tnet/factors/...) or "market"/"self"
  * direction  : -1 down-weight the target voice, +1 up-weight, 0 informational
  * regime     : "any" or a specific regime the belief is conditioned on
  * confidence : 0..1, DECAYS with time since last supporting evidence (half-life)
                 so a stale belief can't suppress a voice forever
  * evidence_count : times the belief has been re-asserted
  * utility    : -1..1, how USEFUL the belief has been as a strategy input
                 (set by the evaluator from realized episode outcomes); untested
                 beliefs stay at advisory authority until they earn more

At signal-aggregation time confluence multiplies each voice weight by
voice_multipliers(regime): a high-confidence, proven belief that contradicts a
voice pulls it toward 0.4x; one that endorses a voice lifts it toward 1.5x.
Pure stdlib, JSON-persisted, fail-soft.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
STORE = PROJ / "data" / "beliefs.json"
VOICES = {"ta", "quant", "fundamental", "ml", "council", "prediction",
          "tnet", "alpha_engine", "cortex", "factors"}
REGIMES = {"risk_on", "risk_off", "high_vol", "neutral", "any"}

_HALFLIFE_DAYS = 14.0          # confidence half-life since last evidence
_MULT_LO, _MULT_HI = 0.4, 1.5  # bounds on a voice's belief multiplier


def _load() -> list:
    try:
        return json.loads(STORE.read_text())
    except Exception:  # noqa: BLE001
        return []


def _save(rows: list) -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(rows, indent=2))
    except Exception:  # noqa: BLE001
        pass


def _norm_claim(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip().lower())[:240]


def _cid(claim: str) -> str:
    return hashlib.sha1(_norm_claim(claim).encode()).hexdigest()[:12]


def _eff_conf(b: dict, now: float | None = None) -> float:
    now = now or time.time()
    age_d = max(0.0, (now - b.get("last_revised", now)) / 86400.0)
    return float(b.get("confidence", 0.0)) * (0.5 ** (age_d / _HALFLIFE_DAYS))


def upsert(claim: str, target: str = "market", direction: int = 0,
           regime: str = "any", confidence: float = 0.55) -> dict:
    """Add or reinforce a belief. Re-asserting the same claim bumps its evidence
    count + confidence and refreshes its recency (resetting decay)."""
    target = target if target in VOICES or target in ("market", "self") else "market"
    regime = regime if regime in REGIMES else "any"
    direction = int(max(-1, min(1, direction)))
    now = time.time()
    rows = _load()
    cid = _cid(claim)
    for b in rows:
        if b["id"] == cid:
            b["evidence_count"] = int(b.get("evidence_count", 1)) + 1
            b["confidence"] = round(min(0.98, 0.6 * b.get("confidence", 0.5) + 0.4 * confidence + 0.03), 4)
            b["last_revised"] = now
            b["target"], b["direction"], b["regime"] = target, direction, regime
            _save(rows)
            return b
    rec = {"id": cid, "claim": claim[:240], "target": target, "direction": direction,
           "regime": regime, "confidence": round(float(confidence), 4),
           "evidence_count": 1, "utility": 0.0, "created": now, "last_revised": now}
    rows.append(rec)
    _save(rows[-200:])
    return rec


def all_beliefs() -> list:
    now = time.time()
    out = []
    for b in _load():
        b = dict(b); b["eff_confidence"] = round(_eff_conf(b, now), 4)
        out.append(b)
    out.sort(key=lambda r: r["eff_confidence"], reverse=True)
    return out


def active(regime: str | None = None, min_conf: float = 0.15) -> list:
    now = time.time()
    reg = regime if regime in REGIMES else "any"
    out = []
    for b in _load():
        if b.get("regime", "any") not in ("any", reg):
            continue
        if _eff_conf(b, now) >= min_conf:
            out.append(b)
    return out


def voice_multipliers(regime: str | None = None) -> dict:
    """Per-voice multiplier from active, regime-matching beliefs. Authority scales
    with decayed confidence and proven utility; untested beliefs act only softly."""
    now = time.time()
    mult: dict[str, float] = {}
    for b in active(regime):
        tgt = b.get("target")
        d = int(b.get("direction", 0))
        if tgt not in VOICES or d == 0:
            continue
        ec = _eff_conf(b, now)
        util = float(b.get("utility", 0.0))
        authority = ec * max(0.3, min(1.0, 0.3 + 0.7 * (util + 1.0) / 2.0))
        if d < 0:
            factor = 1.0 - 0.6 * authority        # toward 0.4x
        else:
            factor = 1.0 + 0.5 * authority        # toward 1.5x
        mult[tgt] = mult.get(tgt, 1.0) * factor
    return {k: round(max(_MULT_LO, min(_MULT_HI, v)), 3) for k, v in mult.items()}


def set_utility(cid: str, utility: float) -> None:
    rows = _load()
    for b in rows:
        if b["id"] == cid:
            b["utility"] = round(max(-1.0, min(1.0, float(utility))), 3)
            _save(rows)
            return


def conflicts() -> list:
    """Belief pairs on the same voice + overlapping regime with opposite direction
    (a dissonance signal that warrants a focused re-examination)."""
    bs = [b for b in all_beliefs() if b.get("direction") and b.get("target") in VOICES
          and b["eff_confidence"] >= 0.25]
    out = []
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            a, c = bs[i], bs[j]
            if a["target"] != c["target"]:
                continue
            regs_overlap = a["regime"] == c["regime"] or "any" in (a["regime"], c["regime"])
            if regs_overlap and (a["direction"] * c["direction"] < 0):
                out.append({"target": a["target"], "a": a["claim"], "b": c["claim"],
                            "score": round(min(a["eff_confidence"], c["eff_confidence"]), 3)})
    return out


def stats() -> dict:
    bs = all_beliefs()
    reg = "neutral"
    try:                                          # show the ACTIVE (current-regime) multipliers
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        pass
    return {"n": len(bs), "regime": reg, "voice_multipliers": voice_multipliers(reg),
            "conflicts": len(conflicts()),
            "top": [{"claim": b["claim"], "target": b["target"], "direction": b["direction"],
                     "regime": b["regime"], "confidence": b["eff_confidence"],
                     "utility": b.get("utility", 0.0), "evidence": b.get("evidence_count", 1)}
                    for b in bs[:8]]}


if __name__ == "__main__":
    upsert("momentum voices fail in high-vol regimes", "tnet", -1, "high_vol", 0.7)
    upsert("ml edge is reliable when calibrated", "ml", +1, "any", 0.6)
    print(json.dumps(stats(), indent=2))
    print("multipliers(high_vol):", voice_multipliers("high_vol"))
