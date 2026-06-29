"""
Trailing-stop exits manager (the "trailing-stop autopilot").

A standalone daemon that watches every OPEN position across the shared paper
account and ratchets a trailing stop behind it (high-water mark via
risk.trailing_stop). When price retraces past the trail, it market-closes the
position. This sits ON TOP of the bracket TP/SL each entry already carries -- the
bracket is the hard backstop; this locks in gains dynamically before that.

State (per-symbol high-water mark) persists to data/exits_state.json so a restart
re-seeds rather than forgetting. Equity + options positions both handled via
broker.close_position. PAPER only.

Run: python -m trader.exits     (TRAIL_PCT=0 disables it)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from . import config
from .broker import AlpacaBroker
from .risk import trailing_stop

STATE_PATH = "data/exits_state.json"
LOG_PATH = "data/exits.log"


def _load() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH))
        except Exception:
            return {}
    return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    json.dump(d, open(STATE_PATH, "w"))


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        open(LOG_PATH, "a").write(line + "\n")
    except Exception:
        pass


def _brain_offside(symbol: str, side: str, asset_class: str, plpc, entry: float,
                   last: float, loss_floor: float) -> str | None:
    """Return a reason string if this position is a losing trade on the wrong
    side of a DEFENSIVE brain posture, else None. Fail-soft: if posture is
    unknown/neutral, returns None (does nothing)."""
    try:
        from . import market_brain
        asset = "crypto" if "crypto" in (asset_class or "").lower() or symbol.upper().endswith("USD") else "equity"
        pos = market_brain.cached_posture(asset) or {}
        sm = float(pos.get("size_mult", 1.0) or 1.0)
        bias = str(pos.get("bias", "")).lower()
        defensive = sm <= 0.85 or "off" in bias or "short" in bias or "defens" in bias
        risk_on = "on" in bias and sm >= 1.0
        # realized P&L% of the position (fallback to entry/last if broker omitted it)
        if plpc is None:
            plpc = (last / entry - 1.0) if side == "buy" else (entry / last - 1.0)
        offside = (side == "buy" and defensive) or (side == "sell" and risk_on)
        if offside and plpc <= -abs(loss_floor):
            return f"plpc={plpc:+.2%} posture={asset}:{bias or 'n/a'} x{sm} (offside in defensive regime)"
    except Exception:  # noqa: BLE001
        return None
    return None


def _announce(symbol: str, side: str, why: str) -> None:
    """Surface a brain exit to the mesh + alerts feed (best-effort)."""
    try:
        from . import mesh
        mesh.publish("exits", "risk_exit", f"cut {side} {symbol} -- {why}", symbol=symbol, salience=0.7)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    cfg = config.load()
    trail = float(os.getenv("TRAIL_PCT", "0.02"))
    poll = int(os.getenv("EXITS_POLL", "30"))
    brain_exits = os.getenv("BRAIN_EXITS", "1").strip().lower() in ("1", "true", "yes", "on")
    brain_loss = float(os.getenv("BRAIN_EXIT_LOSS", "0.012"))   # min loss (frac) to act -- avoid noise
    brain_max = int(os.getenv("BRAIN_EXIT_MAX", "5"))           # cap cuts per cycle (no mass dump)
    pf = os.getenv("BOT_PID_FILE")
    if pf:
        try:
            os.makedirs(os.path.dirname(pf) or ".", exist_ok=True)
            open(pf, "w").write(str(os.getpid()))
        except Exception:
            pass
    if trail <= 0:
        _log("TRAIL_PCT<=0 -> trailing disabled; exiting.")
        return
    broker = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret, paper=True)
    _log(f"exits manager up: trail={trail*100:.2f}% poll={poll}s")
    hwm = _load()
    while True:
        try:
            positions = broker.positions_detailed()
            open_syms = set()
            brain_cut = 0
            for p in positions:
                sym, last, side = p["symbol"], p["current"], p["side"]
                open_syms.add(sym)
                if last is None:
                    continue
                # --- brain-aware risk exit: cut OFFSIDE LOSERS when the desk's
                # posture turns defensive against the position. Pure risk
                # management (no prediction): only fires when a position is both
                # (a) losing past a floor and (b) on the wrong side of a
                # defensive regime. Conservative by design; env-tunable.
                if brain_exits and brain_cut < brain_max:
                    why = _brain_offside(sym, side, p.get("asset_class", ""),
                                         p.get("unrealized_plpc"), p["avg_entry"], last, brain_loss)
                    if why:
                        brain_cut += 1
                        ok = broker.close_position(sym)
                        _log(f"BRAIN EXIT {sym} side={side} {why} closed={ok}")
                        _announce(sym, side, why)
                        hwm.pop(sym, None); open_syms.discard(sym)
                        continue
                stop, h = trailing_stop(p["avg_entry"], last, side, trail, hwm.get(sym))
                hwm[sym] = h
                breached = (side == "buy" and last <= stop) or (side == "sell" and last >= stop)
                if breached:
                    ok = broker.close_position(sym)
                    _log(f"TRAIL EXIT {sym} side={side} last={last} stop={stop} closed={ok}")
                    hwm.pop(sym, None)
                    open_syms.discard(sym)
            hwm = {k: v for k, v in hwm.items() if k in open_syms}
            _save(hwm)
        except Exception as e:
            _log(f"error (continuing): {e}")
        time.sleep(poll)


if __name__ == "__main__":
    main()
