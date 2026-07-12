"""Deep research sweep -- a heavier, scheduled search for a real ML edge than the
routine every-few-hours retrain.

The live autonomy loop retrains one config on a cadence. This module evaluates a
BOUNDED GRID of configurations (forward horizon x feature window x label neutral
band) with the same purged/embargoed cross-validation, ranks them by the honest
lower bound of AUC (mean - 1 sigma), and adopts the best ONLY if it is
statistically meaningful (cv_auc_lo >= 0.51). If nothing clears the bar it adopts
nothing and says so -- no pretending a lucky split is signal.

Designed to be triggered on a schedule (Cloudflare Cron -> /api/research/run) or
autonomously while the market is closed. Bounded so it finishes on the free tier:
the symbol series are fetched once and cached, so the grid mostly reuses them.
"""
from __future__ import annotations

import json
import os
import threading
import time

_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "research"))
_LAST = os.path.join(_DIR, "last.json")
_LOCK = threading.Lock()
_RUNNING = {"on": False, "started": 0.0}

# bounded default grid (horizon, lookback, neutral_band). Kept small for the free
# tier; series are cached so this is mostly CV compute.
_DEFAULT_GRID = [
    (12, 130, 0.01), (15, 130, 0.01), (20, 130, 0.01), (25, 130, 0.01),
    (20, 90, 0.01), (20, 130, 0.005), (20, 130, 0.02),
]


def _eval_config(horizon: int, lookback: int, neutral_band: float) -> dict:
    import numpy as np
    from .ml.dataset import build_dataset
    from .ml.model import LogisticModel
    from . import cv as _cv
    X, y, dates, syms, names = build_dataset(horizon=horizon, lookback=lookback,
                                             neutral_band=neutral_band)
    if len(X) < 300:
        return {"horizon": horizon, "lookback": lookback, "neutral_band": neutral_band,
                "n": len(X), "skip": "too few samples"}
    order = np.argsort(dates)
    X = np.asarray(X)[order]; y = np.asarray(y)[order]; dates = np.asarray(dates)[order]
    res = _cv.cv_auc(
        X, y, dates.tolist(), horizon,
        lambda Xt, yt: LogisticModel(names).fit(Xt, yt, l2=1.0, epochs=400),
        lambda m, Xe: m.proba(Xe), k=5)
    return {"horizon": horizon, "lookback": lookback, "neutral_band": neutral_band,
            "n": int(len(X)), "symbols": len(set(syms)),
            "cv_auc": res.get("cv_auc"), "cv_std": res.get("cv_std"),
            "cv_auc_lo": res.get("cv_auc_lo"), "folds": res.get("folds")}


def deep_sweep(grid=None, adopt: bool = True, min_lo: float = 0.51) -> dict:
    """Evaluate the grid, rank by honest lower-bound AUC, adopt the best only if
    it is statistically meaningful. Returns a full report."""
    t0 = time.time()
    grid = grid or _DEFAULT_GRID
    results = []
    for h, lb, nb in grid:
        try:
            results.append(_eval_config(h, lb, nb))
        except Exception as e:  # noqa: BLE001
            results.append({"horizon": h, "lookback": lb, "neutral_band": nb, "error": str(e)[:100]})

    scored = [r for r in results if r.get("cv_auc_lo") is not None]
    ranked = sorted(scored, key=lambda r: r["cv_auc_lo"], reverse=True)
    best = ranked[0] if ranked else None

    adopted = None
    verdict = "no config evaluated"
    if best:
        if best["cv_auc_lo"] >= min_lo:
            if adopt:
                try:
                    from .ml.train import train_once
                    adopted = train_once(horizon=best["horizon"], lookback=best["lookback"],
                                         force_promote=True)
                except Exception as e:  # noqa: BLE001
                    adopted = {"ok": False, "error": str(e)[:120]}
            verdict = (f"adopted h{best['horizon']}/lb{best['lookback']} "
                       f"(cv_auc {best['cv_auc']}, lo {best['cv_auc_lo']})")
        else:
            verdict = (f"no significant edge -- best was h{best['horizon']} "
                       f"cv_auc {best['cv_auc']} but lo {best['cv_auc_lo']} < {min_lo}; kept champion")

    report = {
        "ok": True, "verdict": verdict, "best": best, "adopted": bool(adopted),
        "adopt_metrics": adopted, "results": ranked + [r for r in results if r not in scored],
        "grid_size": len(grid), "elapsed_s": round(time.time() - t0, 1),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save(report)
    try:
        from . import mesh
        mesh.publish("ml", "research", f"deep sweep: {verdict}", salience=0.7)
    except Exception:  # noqa: BLE001
        pass
    return report


def _save(report: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        json.dump(report, open(_LAST, "w"), indent=2)
    except Exception:  # noqa: BLE001
        pass


def last() -> dict:
    try:
        return json.load(open(_LAST))
    except Exception:  # noqa: BLE001
        return {}


def run_async(grid=None) -> dict:
    """Kick a deep sweep in a background thread (returns immediately). One at a time."""
    with _LOCK:
        if _RUNNING["on"]:
            return {"started": False, "reason": "a research run is already in progress",
                    "since": _RUNNING["started"]}
        _RUNNING["on"] = True
        _RUNNING["started"] = time.time()

    def _worker():
        try:
            deep_sweep(grid=grid)
        finally:
            with _LOCK:
                _RUNNING["on"] = False

    threading.Thread(target=_worker, daemon=True).start()
    return {"started": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def status() -> dict:
    return {"running": _RUNNING["on"], "started": _RUNNING["started"], "last": last()}


if __name__ == "__main__":
    print(json.dumps(deep_sweep(), indent=2))
