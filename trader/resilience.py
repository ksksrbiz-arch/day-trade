"""
Execution resilience -- assume every tool/API/model fails sometimes.

  * call(fn, kind=...) retries by TOOL TYPE (llm/network/broker/data) with
    exponential backoff + jitter, respects a time BUDGET, and falls back to an
    alternate route if all attempts fail. Returns a structured result with an
    attempt count, a fell_back flag, and a CONFIDENCE score derived from how
    cleanly it succeeded.
  * Checkpoint persists run state to disk so a crashed run resumes instead of
    restarting from zero (idempotent).
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
CKPT_DIR = PROJ / "data" / "checkpoints"

# tries, base_delay(s), max_delay(s)
RETRY = {
    "llm": (2, 1.0, 8.0),
    "network": (3, 0.5, 4.0),
    "broker": (2, 0.5, 3.0),
    "data": (2, 0.5, 4.0),
    "default": (2, 0.5, 4.0),
}


def backoff_delay(kind: str, attempt: int) -> float:
    _, base, mx = RETRY.get(kind, RETRY["default"])
    return min(mx, base * (2 ** attempt)) + random.uniform(0, base)


def confidence(attempts: int, fell_back: bool, ok: bool) -> float:
    """How much to trust a result: clean first-try success = 1.0, retries and
    fallbacks erode it, failure floors it."""
    if not ok:
        return 0.0
    c = 1.0 - 0.2 * max(0, attempts - 1)
    if fell_back:
        c -= 0.3
    return round(max(0.1, min(1.0, c)), 2)


def call(fn, kind: str = "network", budget_s: float = None, fallback=None) -> dict:
    """Run fn with retry/backoff by tool type, a time budget, and a fallback
    route. Returns {ok, value, attempts, fell_back, confidence, error, kind}."""
    tries = RETRY.get(kind, RETRY["default"])[0]
    start = time.time()
    last = None
    for attempt in range(tries):
        if budget_s is not None and (time.time() - start) >= budget_s:
            last = "time budget exhausted"
            break
        try:
            v = fn()
            return {"ok": True, "value": v, "attempts": attempt + 1, "fell_back": False,
                    "confidence": confidence(attempt + 1, False, True), "error": None, "kind": kind}
        except Exception as e:
            last = str(e)[:160]
            if attempt < tries - 1:
                time.sleep(backoff_delay(kind, attempt))
    # all primary attempts failed -> fallback route
    if fallback is not None:
        try:
            v = fallback()
            return {"ok": True, "value": v, "attempts": tries, "fell_back": True,
                    "confidence": confidence(tries, True, True), "error": last, "kind": kind}
        except Exception as e:
            last = f"primary: {last} | fallback: {str(e)[:120]}"
    return {"ok": False, "value": None, "attempts": tries, "fell_back": fallback is not None,
            "confidence": 0.0, "error": last, "kind": kind}


class Checkpoint:
    """Idempotent run-state checkpoint. set()/get() persist immediately so a
    crash mid-run resumes from the last good state."""
    def __init__(self, name: str):
        CKPT_DIR.mkdir(parents=True, exist_ok=True)
        self.path = CKPT_DIR / f"{name}.json"
        self.state = {}
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text())
            except Exception:
                self.state = {}

    def get(self, key, default=None):
        return self.state.get(key, default)

    def set(self, key, value):
        self.state[key] = value
        self.path.write_text(json.dumps(self.state))

    def done(self, step: str) -> bool:
        return step in self.state.get("_done", [])

    def mark(self, step: str):
        d = self.state.setdefault("_done", [])
        if step not in d:
            d.append(step)
        self.path.write_text(json.dumps(self.state))
