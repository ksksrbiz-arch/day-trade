"""Governor: the safety chokepoint for autonomous actions.

Agents may freely run read/analysis tools. Any *mutating* action -- above all a
change to the operating scheme -- passes through here, which enforces hard
bounds, records an auditable activity entry, and (for param changes) writes a
bounded override that config.load() merges. No real-money path exists anywhere;
this is paper-only by construction.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "agents"))
ACTIVITY = os.path.join(_DATA, "activity.jsonl")
OVERRIDES = os.path.join(_DATA, "overrides.json")

# The knobs agents are allowed to tune, with HARD bounds. Anything outside is
# clamped and flagged; anything not listed is denied.
PARAM_BOUNDS = {
    "CONFLUENCE_MIN_SCORE": (0.10, 0.45),
    "CONFLUENCE_MIN_AGREE": (2, 4),
    "MIN_CONFIDENCE": (0.45, 0.85),
    "MIN_SENTIMENT": (0.15, 0.60),
    "COOLDOWN_MIN": (5, 120),
    "TRAIL_PCT": (0.02, 0.12),
}
BOOL_PARAMS = {"ALLOW_SHORT"}


def _log(entry: dict):
    os.makedirs(_DATA, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    with open(ACTIVITY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    # best-effort long-term memory
    try:
        from . import memory
        if entry.get("kind") in ("action", "proposal"):
            memory.remember(f"{entry.get('agent','?')}: {entry.get('summary','')}",
                            {"kind": entry.get("kind")})
    except Exception:  # noqa: BLE001
        pass
    return entry


def log_observation(agent: str, text: str, data: dict | None = None):
    return _log({"kind": "observation", "agent": agent, "summary": text[:300],
                 "data": data or {}})


def load_overrides() -> dict:
    if not os.path.exists(OVERRIDES):
        return {}
    try:
        return json.load(open(OVERRIDES))
    except Exception:  # noqa: BLE001
        return {}


def propose_param(agent: str, name: str, value, rationale: str = "") -> dict:
    """Bounded change to the operating scheme. Clamps to PARAM_BOUNDS; denies unknown."""
    name = name.upper()
    if name in BOOL_PARAMS:
        val = str(value).strip().lower() in ("1", "true", "yes", "on") or value is True
        ov = load_overrides(); prev = ov.get(name); ov[name] = val
        import os as _os
        _os.makedirs(_DATA, exist_ok=True)
        json.dump(ov, open(OVERRIDES, "w"), indent=2)
        return _log({"kind": "action", "agent": agent, "param": name,
                     "from": prev, "to": val, "bounded": False,
                     "summary": f"set {name} {prev} -> {val} ({rationale[:120]})"})
    if name not in PARAM_BOUNDS:
        return _log({"kind": "denied", "agent": agent, "param": name,
                     "summary": f"param {name} not governable"})
    lo, hi = PARAM_BOUNDS[name]
    try:
        val = type(lo)(value)
    except (TypeError, ValueError):
        return _log({"kind": "denied", "agent": agent, "param": name,
                     "summary": f"bad value {value!r} for {name}"})
    clamped = max(lo, min(hi, val))
    ov = load_overrides()
    prev = ov.get(name)
    ov[name] = clamped
    os.makedirs(_DATA, exist_ok=True)
    json.dump(ov, open(OVERRIDES, "w"), indent=2)
    return _log({"kind": "action", "agent": agent, "param": name,
                 "from": prev, "to": clamped, "bounded": clamped != val,
                 "summary": f"set {name} {prev} -> {clamped} ({rationale[:120]})"})


def record_action(agent: str, tool: str, result_summary: str, data: dict | None = None):
    return _log({"kind": "action", "agent": agent, "tool": tool,
                 "summary": result_summary[:300], "data": data or {}})


def recent_activity(n: int = 40) -> list[dict]:
    if not os.path.exists(ACTIVITY):
        return []
    out = []
    for ln in open(ACTIVITY, encoding="utf-8").read().splitlines()[-n:]:
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                pass
    return list(reversed(out))


if __name__ == "__main__":
    print(propose_param("quant", "CONFLUENCE_MIN_SCORE", 0.9, "test clamp"))  # clamps to 0.45
    print(propose_param("quant", "FOO", 1, "unknown"))                         # denied
    print("overrides:", load_overrides())
