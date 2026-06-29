"""Train pipeline with champion/challenger promotion -- the engine of safe,
continuous improvement.

  1. Build the dataset from CRSP-lite (forward-return labels).
  2. Split TIME-ordered (train on older, validate on the most recent slice) so
     evaluation never sees its own future.
  3. Fit a challenger; measure held-out AUC, accuracy, and *edge* (mean forward
     win-rate of high-conviction picks vs base rate).
  4. Load the incumbent champion; PROMOTE the challenger only if it beats the
     champion's validation AUC by a margin. Otherwise keep the champion.
  5. Append every run to history.json for an auditable improvement curve.

Run repeatedly (daemon / scheduled task) and the model only ever gets better.
"""
from __future__ import annotations

import json
import os
import time
import numpy as np

from .dataset import build_dataset
from .model import LogisticModel, MODEL_PATH, auc, accuracy

_DATA = os.path.dirname(MODEL_PATH)
HISTORY = os.path.join(_DATA, "history.json")
PROMOTE_MARGIN = 0.002          # challenger must beat champion AUC by this


def _edge(y, p, top_frac=0.3):
    """Win-rate of the top `top_frac` highest-prob picks minus the base rate."""
    y = np.asarray(y); p = np.asarray(p)
    if len(y) == 0:
        return 0.0
    k = max(1, int(len(p) * top_frac))
    idx = np.argsort(p)[-k:]
    return float(y[idx].mean() - y.mean())


def train_once(horizon=10, lookback=60, val_frac=0.25, l2=1.0, epochs=500,
               verbose=True) -> dict:
    X, y, dates, syms, names = build_dataset(horizon=horizon, lookback=lookback)
    # learn from the system's OWN matured trades (closed feedback loop)
    n_trades = 0
    try:
        from .outcomes import trade_samples
        tX, ty, td, ts_ = trade_samples(horizon=horizon, lookback=lookback)
        if tX:
            X = X + tX; y = y + ty; dates = dates + td; syms = syms + ts_
            n_trades = len(tX)
    except Exception:  # noqa: BLE001
        pass
    if len(X) < 200:
        return {"ok": False, "reason": f"too few samples ({len(X)})"}

    # time-ordered split
    order = np.argsort(dates)
    X = np.asarray(X)[order]; y = np.asarray(y)[order]
    cut = int(len(X) * (1 - val_frac))
    Xtr, ytr, Xva, yva = X[:cut], y[:cut], X[cut:], y[cut:]

    chal = LogisticModel(names).fit(Xtr, ytr, l2=l2, epochs=epochs)
    pva = chal.proba(Xva)
    metrics = {
        "auc": round(auc(yva, pva), 4),
        "acc": round(accuracy(yva, pva), 4),
        "edge": round(_edge(yva, pva), 4),
        "n_train": int(len(Xtr)), "n_val": int(len(Xva)),
        "base_rate": round(float(yva.mean()), 4),
        "horizon": horizon, "lookback": lookback,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbols": len(set(syms)),
        "trade_samples": n_trades,
    }
    chal.meta = metrics

    champ = LogisticModel.load(MODEL_PATH)
    champ_auc = champ.meta.get("auc", 0.0) if champ else 0.0
    promote = champ is None or metrics["auc"] >= champ_auc + PROMOTE_MARGIN
    metrics["champion_auc"] = round(champ_auc, 4)
    metrics["promoted"] = bool(promote)

    if promote:
        chal.save(MODEL_PATH)

    # append history
    os.makedirs(_DATA, exist_ok=True)
    hist = []
    if os.path.exists(HISTORY):
        try:
            hist = json.load(open(HISTORY))
        except Exception:  # noqa: BLE001
            hist = []
    hist.append(metrics)
    json.dump(hist[-200:], open(HISTORY, "w"), indent=2)

    if verbose:
        print(f"challenger AUC={metrics['auc']} acc={metrics['acc']} edge={metrics['edge']:+.3f} "
              f"(base {metrics['base_rate']})  champion AUC={champ_auc} "
              f"-> {'PROMOTED' if promote else 'kept champion'}")
        if promote:
            print("  top features:", dict(list(chal.importances().items())[:6]))
    return {"ok": True, **metrics}


if __name__ == "__main__":
    import sys
    h = 10
    for i, a in enumerate(sys.argv):
        if a == "--horizon" and i + 1 < len(sys.argv):
            h = int(sys.argv[i + 1])
    train_once(horizon=h)
