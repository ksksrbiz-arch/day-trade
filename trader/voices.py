"""Voice controls -- human-in-the-loop overrides for the confluence brain.

Closes the loop between attribution ("which voice makes money") and the weights
the system actually trades on. A small persisted override store lets you:

  * MUTE  a voice  -> it stops voting entirely (dropped from confluence).
  * PIN   a voice  -> its weight is locked at a fixed value, ignoring the
                      backprop-learned emphasis and the static base (regime
                      multipliers still apply).

confluence() honors these every decision. summary() assembles a per-voice view
(base / learned / effective weight, attribution P&L, agree-accuracy, state) for
the dashboard Voices panel.
"""
from __future__ import annotations

import json
import os
import time

METHODS = ["ta", "quant", "fundamental", "ml", "council", "prediction", "tnet"]

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "voices"))
STORE = os.path.join(_DATA, "overrides.json")

_cache = {"ts": 0.0, "val": None}
TTL = 15.0


def _load() -> dict:
    try:
        with open(STORE) as f:
            d = json.load(f)
        return {"muted": list(d.get("muted", [])), "pinned": dict(d.get("pinned", {}))}
    except Exception:  # noqa: BLE001
        return {"muted": [], "pinned": {}}


def _save(d: dict):
    os.makedirs(_DATA, exist_ok=True)
    with open(STORE, "w") as f:
        json.dump(d, f, indent=2)
    _cache["val"] = None        # invalidate


def overrides() -> dict:
    """Cached {muted:set, pinned:dict} for the hot confluence path."""
    now = time.time()
    if _cache["val"] is not None and now - _cache["ts"] < TTL:
        return _cache["val"]
    d = _load()
    val = {"muted": set(d["muted"]), "pinned": {k: float(v) for k, v in d["pinned"].items()}}
    _cache["val"] = val
    _cache["ts"] = now
    return val


def set_mute(voice: str, on: bool = True) -> dict:
    if voice not in METHODS:
        return {"error": f"unknown voice {voice}"}
    d = _load()
    m = set(d["muted"])
    m.discard(voice)
    if on:
        m.add(voice)
    d["muted"] = sorted(m)
    _save(d)
    return d


def set_pin(voice: str, weight: float | None) -> dict:
    if voice not in METHODS:
        return {"error": f"unknown voice {voice}"}
    d = _load()
    if weight is None:
        d["pinned"].pop(voice, None)
    else:
        d["pinned"][voice] = max(0.0, min(1.0, float(weight)))
    _save(d)
    return d


def summary(regime: str | None = None) -> dict:
    """Per-voice view for the dashboard: base / learned / effective weights,
    attribution P&L, agree-accuracy, and override state."""
    from . import alpha
    rw = alpha._REGIME_W.get(regime or "neutral", alpha._REGIME_W["neutral"])
    learned = None
    try:
        from . import backprop
        learned = backprop.learned_emphasis()
    except Exception:  # noqa: BLE001
        learned = None
    ov = overrides()

    # attribution P&L per voice (best-effort)
    attr = {}
    try:
        from . import attribution
        for v in attribution.report().get("voices", []):
            attr[v["voice"]] = v
    except Exception:  # noqa: BLE001
        pass

    # effective base weight per active voice (mirrors confluence logic)
    active = [m for m in METHODS if m not in ov["muted"]]
    eff_base = {}
    for m in active:
        if m in ov["pinned"]:
            eff_base[m] = ov["pinned"][m] * rw.get(m, 1.0)
        elif learned and m in learned:
            eff_base[m] = learned[m] * rw.get(m, 1.0)
        else:
            eff_base[m] = alpha._BASE_W.get(m, 0.12) * rw.get(m, 1.0)
    tot = sum(eff_base.values()) or 1.0

    voices = []
    for m in METHODS:
        a = attr.get(m, {})
        voices.append({
            "voice": m,
            "base": round(alpha._BASE_W.get(m, 0.12), 3),
            "learned": (round(learned[m], 3) if learned and m in learned else None),
            "effective": (round(eff_base[m] / tot, 3) if m in eff_base else 0.0),
            "muted": m in ov["muted"],
            "pinned": (round(ov["pinned"][m], 3) if m in ov["pinned"] else None),
            "attributed_return_pct": a.get("attributed_return_pct"),
            "lead_hit_rate": a.get("lead_hit_rate"),
            "verdict": a.get("verdict"),
        })
    return {"regime": regime or "neutral",
            "weights_source": "backprop-learned" if learned else "static base",
            "voices": voices}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))
