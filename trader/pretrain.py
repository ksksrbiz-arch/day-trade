"""Historical pretraining / cold-start for the fusion brain.

The cortex (neural core) and the confluence backprop learner both train on
``data/backprop/decisions.jsonl`` -- rows of (method-score vector -> realized
forward return). Live, that file fills ONE decision at a time, so the fusion
layers starve for weeks before they can even train (they need >= 30 resolved
rows). That is why the terminal shows "cortex not trained yet" and the ML edge
never gets a fused second opinion.

This module BOOTSTRAPS that store from history. It walks daily bars for a
universe and, at each historical day, computes the PRICE-DERIVED voices
(ta, quant, ml, tnet) using ONLY the window up to that day -- strictly no
look-ahead -- then logs a decision dated in the past. Because the day is
historical, ``backprop.build_dataset()`` resolves the forward return
immediately, so thousands of labeled rows exist at once and cortex + confluence
can train right away.

Honest scope (so nobody over-reads this):
  * It bootstraps the FUSION layer -- how to weight/combine the price voices --
    from real historical outcomes. It does NOT fabricate alpha.
  * It intentionally omits the news/council/fundamental/prediction voices:
    reconstructing those historically would need point-in-time news we don't
    have. They stay 0 in backfilled rows and keep being learned live.
  * Features are strictly point-in-time; only the label looks forward. The ml
    voice uses the current model on a past window (mild parameter leakage, no
    label leakage) -- acceptable for a fusion-layer cold start.
  * Idempotent: ``backprop.log_decision`` de-dups by symbol|day, so re-running
    only adds genuinely new rows.
"""
from __future__ import annotations

import os
import time

# A broad, liquid universe (large-cap equities + core ETFs). Kept modest so a
# one-shot bootstrap finishes fast on the free tier.
DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
    "TSLA", "JPM", "XOM", "UNH", "GLD", "TLT", "HYG", "AMD", "NFLX", "BAC",
    "WMT", "COST", "AVGO", "CRM", "ORCL", "ADBE", "PEP", "KO", "DIS", "INTC",
]

_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "pretrain.json")


def _tnet_voice(win) -> float | None:
    """Point-in-time transformer directional read on the given window."""
    try:
        import numpy as np
        from . import tnet
        rd = tnet.directional_readout(np.asarray(win[-256:], dtype=float))
        raw = float(rd.get("raw", 0.0))
        agree = float(rd.get("agree", 0.0))
        return max(-1.0, min(1.0, raw * (0.5 + 0.5 * agree)))
    except Exception:  # noqa: BLE001
        return None


def _voices(win) -> dict:
    """Compute the price-derived voice vector from a point-in-time close window."""
    scores: dict[str, float] = {}
    try:
        from . import ta as _ta
        sig = _ta.ta_signals(win)
        if sig is not None and getattr(sig, "score", None) is not None:
            scores["ta"] = float(sig.score)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import quant as _q
        ns = _q.name_stats(win)
        if ns is not None and ns.quant_score is not None:
            scores["quant"] = float(ns.quant_score)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .ml import infer as _ml
        v = _ml.score_from_closes(win)
        if v is not None:
            scores["ml"] = float(v)
    except Exception:  # noqa: BLE001
        pass
    tv = _tnet_voice(win)
    if tv is not None:
        scores["tnet"] = tv
    return scores


def backfill(universe=None, horizon: int = 10, step: int = 4, warmup: int = 70,
             max_symbols: int = 30) -> dict:
    """Walk history and log point-in-time decisions. Returns a backfill report."""
    from . import backprop
    from .ml.dataset import _alpaca_series

    uni = (universe or DEFAULT_UNIVERSE)[:max_symbols]
    logged = 0
    syms_used = 0
    skipped: list[str] = []
    for sym in uni:
        try:
            ser = _alpaca_series(sym)
        except Exception:  # noqa: BLE001
            ser = []
        if len(ser) < warmup + horizon + 5:
            skipped.append(sym)
            continue
        syms_used += 1
        dates = [d for d, _ in ser]
        closes = [float(c) for _, c in ser]
        # leave `horizon` bars of headroom so every logged day resolves to a label
        for idx in range(warmup, len(closes) - horizon, step):
            win = closes[: idx + 1]
            scores = _voices(win)
            if len(scores) < 2:            # need at least a couple of real voices
                continue
            if backprop.log_decision(sym, scores, day=dates[idx],
                                     ref_price=win[-1], horizon=horizon, asset="equity"):
                logged += 1
    return {"logged": logged, "symbols_used": syms_used,
            "symbols_skipped": skipped, "horizon": horizon, "step": step}


def run(universe=None, horizon: int = 10, step: int = 4, warmup: int = 70,
        max_symbols: int = 30, do_train: bool = True) -> dict:
    """Full cold-start: backfill historical decisions, then train the fusion
    layers (confluence backprop + cortex) and calibrate the transformer.

    Safe + idempotent: paper-only, writes only to the training data store, and
    training is champion/challenger-gated inside cortex.train()."""
    t0 = time.time()
    bf = backfill(universe, horizon=horizon, step=step, warmup=warmup, max_symbols=max_symbols)

    out: dict = {"ok": True, "backfill": bf, "trained": {}}
    if do_train:
        # confluence weight-learner (single-layer backprop over the voices)
        try:
            from . import backprop
            out["trained"]["confluence"] = backprop.train()
        except Exception as e:  # noqa: BLE001
            out["trained"]["confluence"] = {"ok": False, "error": str(e)[:160]}
        # neural core (deep ensemble MLP), champion/challenger gated
        try:
            from . import cortex
            out["trained"]["cortex"] = cortex.train()
        except Exception as e:  # noqa: BLE001
            out["trained"]["cortex"] = {"ok": False, "error": str(e)[:160]}
        # transformer calibration (maps the readout to empirical P(up))
        try:
            from . import tnet
            out["trained"]["tnet_calibration"] = tnet.calibrate()
        except Exception as e:  # noqa: BLE001
            out["trained"]["tnet_calibration"] = {"ok": False, "error": str(e)[:160]}

    out["elapsed_s"] = round(time.time() - t0, 1)
    out["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        import json
        os.makedirs(os.path.dirname(_STATE), exist_ok=True)
        json.dump(out, open(os.path.abspath(_STATE), "w"), indent=2)
    except Exception:  # noqa: BLE001
        pass
    # announce to the mesh so the /brain feed shows the cold-start happened
    try:
        from . import mesh
        c = out["trained"].get("cortex", {})
        mesh.publish("ml", "pretrain",
                     f"cold-start: backfilled {bf['logged']} historical decisions across "
                     f"{bf['symbols_used']} symbols; cortex trained={c.get('ok', False)}",
                     salience=0.8)
    except Exception:  # noqa: BLE001
        pass
    return out


def status() -> dict:
    """Last cold-start report + current decision-store size."""
    st: dict = {}
    try:
        import json
        st = json.load(open(os.path.abspath(_STATE)))
    except Exception:  # noqa: BLE001
        st = {}
    try:
        from . import backprop
        n = 0
        if os.path.exists(backprop.DECISIONS):
            n = sum(1 for _ in open(backprop.DECISIONS, encoding="utf-8"))
        st["decisions_logged"] = n
    except Exception:  # noqa: BLE001
        pass
    return st


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
