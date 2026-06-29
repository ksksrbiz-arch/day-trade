"""
Safety lock -- the closed-loop kill switch.

The autotuner runs the adversarial eval suite daily/each cycle. If ANY eval
fails (a guardrail regressed), it sets a lock here; every order in _execute then
hard-DENIES until a human clears it. This is "lock the logic path until reviewed"
made concrete and un-bypassable.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
LOCK = PROJ / "data" / "safety_lock.json"


def lock_active() -> bool:
    return LOCK.exists()


def lock_reason() -> str:
    try:
        return json.loads(LOCK.read_text()).get("reason", "")
    except Exception:
        return "locked"


def set_lock(reason: str):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"reason": reason, "ts": time.time()}))


def clear_lock():
    try:
        LOCK.unlink(missing_ok=True)
    except Exception:
        pass
