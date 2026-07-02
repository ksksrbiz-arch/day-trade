"""Continuous-improvement daemon: retrain on a fixed cadence. Champion/
challenger inside train_once() guarantees the live model only ever improves.
Run detached (start_all.ps1) or as a scheduled task.

  python -m trader.ml.daemon --every 6      # retrain every 6 hours
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import sys
import time

from .train import train_once


_CORE = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
         "META", "TSLA", "JPM", "XOM", "UNH", "GLD", "TLT", "HYG",
         "BTC/USD", "ETH/USD", "SOL/USD"]


def _calib_universe() -> list[str]:
    """Diverse symbol set for the calibration sweep: configured UNIVERSE if set,
    otherwise a liquid cross-asset basket. Capped to keep the sweep bounded."""
    try:
        from .. import config
        uni = list(getattr(config.load(), "universe", []) or [])
    except Exception:  # noqa: BLE001
        uni = []
    out, seen = [], set()
    for s in (uni + _CORE):
        s = str(s).upper().strip()
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out[:24]


def main():
    every_h = 6.0
    for i, a in enumerate(sys.argv):
        if a == "--every" and i + 1 < len(sys.argv):
            every_h = float(sys.argv[i + 1])
    print(f"[ml.daemon] retraining every {every_h}h (champion/challenger gated)")
    while True:
        try:
            from .agent_reliability import reconcile
            rec = reconcile()
            print(f"[ml.daemon] agent reconcile -> {rec}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] reconcile error: {e}")
        try:
            from .. import backprop
            bp = backprop.train()
            print(f"[ml.daemon] backprop -> {bp}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] backprop error: {e}")
        try:
            from .. import tnet
            # sweep a diverse universe so calibration learns from varied scores
            # (not just SPY) -- each forecast() logs itself for forward resolution
            syms = _calib_universe()
            logged = 0
            for sym in syms:
                try:
                    if "error" not in tnet.forecast(sym):
                        logged += 1
                except Exception:  # noqa: BLE001
                    pass
            cal = tnet.calibrate()
            print(f"[ml.daemon] tnet swept {logged}/{len(syms)} -> calibrate {cal}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] tnet calibrate error: {e}")
        try:
            r = train_once()
            print(f"[ml.daemon] {time.strftime('%Y-%m-%d %H:%M')} -> {r}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] error: {e}")
        try:
            from .. import hypolab
            hl = hypolab.run(6)
            print(f"[ml.daemon] hypothesis lab -> winner={hl.get('winner')} promoted={hl.get('promoted')}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] hypolab error: {e}")
        try:
            from .. import cortex
            cx = cortex.train()
            print(f"[ml.daemon] neural core -> {cx}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] cortex error: {e}")
        try:
            from .. import edge
            er = edge.write_and_publish()
            print(f"[ml.daemon] edge report -> {er['counts']}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] edge report error: {e}")
        try:
            from .. import attribution
            ar = attribution.write_and_publish()
            print(f"[ml.daemon] attribution -> resolved {ar['resolved']}")
        except Exception as e:  # noqa: BLE001
            print(f"[ml.daemon] attribution error: {e}")
        time.sleep(every_h * 3600)


if __name__ == "__main__":
    main()
