"""
Beta-capture floor -- the honest cure for "losing to SPY".

When the desk has no proven alpha, decaying beside a rising market is the worst
outcome. So we hold a CORE index position (default SPY at 50% of equity) that
the account tracks; conviction trades are the *deviation* from that beta, not
the whole book. "If you can't beat the index, own it." Paper-only, rate-limited,
capped per rebalance, and fully switch-off-able.

Env:
  BETA_FLOOR      master switch (default on)
  BETA_SYMBOL     core index symbol (default SPY)
  BETA_TARGET     target core allocation fraction of equity (default 0.5)
  BETA_REBAL_MIN  minutes between rebalances (default 60)
  BETA_MAX_STEP   max fraction of equity to add per rebalance (default 0.25)
"""
from __future__ import annotations

import os
import time


def enabled() -> bool:
    return os.getenv("BETA_FLOOR", "1").strip().lower() in ("1", "true", "yes", "on")


def _publish(msg: str):
    try:
        from . import mesh
        mesh.publish("execution", "beta", msg, salience=0.5)
    except Exception:  # noqa: BLE001
        pass


def rebalance(cfg, broker) -> dict:
    """Nudge the core index position toward its target when underweight. Never
    force-sells (lets any winners run); just captures beta with idle cash."""
    if not enabled():
        return {"skipped": "disabled"}
    try:
        from .agents import state
    except Exception:  # noqa: BLE001
        state = None
    now = time.time()
    if state is not None:
        last = float(state.kv_get("beta_last", 0) or 0)
        if now - last < float(os.getenv("BETA_REBAL_MIN", "60")) * 60:
            return {"skipped": "cooldown"}
    try:
        sym = os.getenv("BETA_SYMBOL", "SPY").upper()
        target_frac = float(os.getenv("BETA_TARGET", "0.5"))
        max_step = float(os.getenv("BETA_MAX_STEP", "0.25"))
        equity = float(broker.account_value())
        if equity <= 0:
            return {"skipped": "no equity"}
        held = 0.0
        for p in broker.positions_detailed():
            if p.get("symbol") == sym and (p.get("qty") or 0) > 0:
                px = p.get("current") or p.get("avg_entry") or 0
                held += float(p["qty"]) * float(px)
        target = target_frac * equity
        band = 0.05 * equity
        if held < target - band:
            amt = round(min(target - held, max_step * equity), 2)
            oid = broker.buy_plain(sym, amt)
            if state is not None:
                state.kv_set("beta_last", now)
            if oid:                              # EPISODIC MEMORY: the beta floor is a real long
                try:
                    from . import run as _run
                    px = broker.last_price(sym)
                    _run._log_episode(sym, "buy", px)
                except Exception:  # noqa: BLE001
                    pass
            _publish(f"beta floor: +${amt:.0f} {sym} (core ${held:.0f}->target ${target:.0f}); "
                     f"tracks market when alpha is unproven")
            return {"action": "buy", "symbol": sym, "amount": amt,
                    "held": round(held, 2), "target": round(target, 2), "order": oid}
        if state is not None:
            state.kv_set("beta_last", now)
        return {"action": "hold", "held": round(held, 2), "target": round(target, 2)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}
