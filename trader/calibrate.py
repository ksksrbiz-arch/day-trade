"""
Probability calibration + meta-labeling.

CALIBRATION. A model can rank well (good AUC) yet output miscalibrated
probabilities -- it says 0.8 but is right 0.6 of the time. Kelly sizing and any
probability gate need TRUSTWORTHY probabilities, so we fit a monotone map from
raw score -> empirical P(up):
  * Platt scaling   -- a 1-D logistic (good with little data, assumes a sigmoid).
  * Isotonic (PAV)  -- a non-parametric monotone step fit (best with more data).

META-LABELING (Lopez de Prado). Predicting DIRECTION is hard; predicting whether
to ACT on a primary signal is easier and directly controls bet size. The
MetaLabeler is a logistic model over the voice vector that outputs P(the trade
is a winner). Trained on the same resolved-decision store the cortex uses, it
gives a calibrated confidence that feeds fractional-Kelly sizing and gating.

Pure NumPy/stdlib. Fail-soft: an untrained calibrator is the identity.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

_DATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "calibrate"))
META_PATH = os.path.join(_DATA, "meta.json")
CAL_PATH = os.path.join(_DATA, "calibrators.json")
METHODS = ["ta", "quant", "fundamental", "ml", "council", "prediction", "tnet", "alpha_engine"]


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


# ------------------------------- Platt ------------------------------------ #
def platt_fit(scores, outcomes, epochs=500, lr=0.3) -> dict:
    x = np.asarray(scores, float); y = np.asarray(outcomes, float)
    a, b = 1.0, 0.0
    n = len(x)
    if n < 10:
        return {"a": 1.0, "b": 0.0, "n": n}
    for _ in range(epochs):
        p = _sigmoid(a * x + b)
        ga = float(((p - y) * x).mean()); gb = float((p - y).mean())
        a -= lr * ga; b -= lr * gb
    return {"a": round(a, 5), "b": round(b, 5), "n": n}


def platt_apply(cal: dict, raw: float) -> float:
    return float(_sigmoid(cal.get("a", 1.0) * raw + cal.get("b", 0.0)))


# ----------------------------- Isotonic (PAV) ----------------------------- #
def isotonic_fit(scores, outcomes) -> dict:
    """Pool-Adjacent-Violators monotone regression. Returns breakpoints."""
    order = np.argsort(np.asarray(scores, float))
    x = np.asarray(scores, float)[order]; y = np.asarray(outcomes, float)[order]
    n = len(x)
    if n < 10:
        return {"x": [], "y": []}
    yhat = y.astype(float).copy(); w = np.ones(n)
    i = 0
    while i < n - 1:
        if yhat[i] > yhat[i + 1] + 1e-12:
            new = (w[i] * yhat[i] + w[i + 1] * yhat[i + 1]) / (w[i] + w[i + 1])
            yhat[i] = yhat[i + 1] = new; w[i] += w[i + 1]
            # merge and back up
            yhat = np.delete(yhat, i + 1); w = np.delete(w, i + 1); x = np.delete(x, i + 1)
            n -= 1
            if i > 0:
                i -= 1
        else:
            i += 1
    return {"x": [round(float(v), 5) for v in x], "y": [round(float(v), 5) for v in yhat]}


def isotonic_apply(cal: dict, raw: float) -> float:
    xs, ys = cal.get("x", []), cal.get("y", [])
    if not xs:
        return float(_sigmoid(raw))
    return float(np.interp(raw, xs, ys))


# --------------------------- Meta-labeler --------------------------------- #
class MetaLabeler:
    """Logistic over the voice vector -> P(trade is a winner). Calibrated."""

    def __init__(self, w=None, b=0.0, cal=None, meta=None):
        self.w = np.asarray(w, float) if w is not None else np.zeros(len(METHODS))
        self.b = float(b); self.cal = cal or {}; self.meta = meta or {}

    def p(self, scores: dict) -> float:
        x = np.asarray([float(scores.get(m, 0.0) or 0.0) for m in METHODS])
        raw = float(_sigmoid(x @ self.w + self.b))
        if self.cal.get("x"):
            return isotonic_apply(self.cal, x @ self.w + self.b)
        return raw

    def save(self):
        os.makedirs(_DATA, exist_ok=True)
        json.dump({"w": self.w.tolist(), "b": self.b, "cal": self.cal, "meta": self.meta},
                  open(META_PATH, "w"), indent=2)

    @classmethod
    def load(cls):
        try:
            d = json.load(open(META_PATH))
            return cls(d["w"], d["b"], d.get("cal"), d.get("meta"))
        except Exception:  # noqa: BLE001
            return None


def train(epochs=800, lr=0.3, l2=0.3) -> dict:
    """Fit the meta-labeler + an isotonic calibrator on the resolved decision
    store (the same data the cortex/confluence learn from)."""
    from . import backprop
    X, y = backprop.build_dataset()
    n = len(X)
    if n < 30:
        return {"ok": False, "reason": f"need 30+ resolved decisions, have {n}"}
    X = np.asarray(X, float); y = np.asarray(y, float)
    w = np.zeros(X.shape[1]); b = 0.0
    pos = max(1, int(y.sum())); neg = max(1, n - pos)
    sw = np.where(y == 1, n / (2 * pos), n / (2 * neg)); sw /= sw.mean()
    for _ in range(epochs):
        p = _sigmoid(X @ w + b); g = (p - y) * sw
        w -= lr * (X.T @ g / n + l2 * w / n); b -= lr * g.mean()
    raw = X @ w + b
    cal = isotonic_fit(raw.tolist(), y.tolist())
    p = _sigmoid(raw)
    acc = float(((p >= 0.5).astype(int) == y).mean())
    # Brier score before/after calibration (lower = better calibrated)
    pc = np.asarray([isotonic_apply(cal, float(r)) for r in raw])
    brier_raw = float(((p - y) ** 2).mean()); brier_cal = float(((pc - y) ** 2).mean())
    meta = {"n": n, "acc": round(acc, 4), "brier_raw": round(brier_raw, 4),
            "brier_cal": round(brier_cal, 4), "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ml = MetaLabeler(w, b, cal, meta); ml.save()
    try:
        from . import mesh
        mesh.publish("ml", "calibrate",
                     f"meta-labeler trained: n={n} acc={acc:.0%} Brier {brier_raw:.3f}->{brier_cal:.3f}",
                     salience=0.6)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, **meta}


def p_correct(scores: dict) -> float | None:
    ml = MetaLabeler.load()
    return ml.p(scores) if ml else None


def card() -> dict:
    ml = MetaLabeler.load()
    return {"trained": bool(ml), **(ml.meta if ml else {})}


if __name__ == "__main__":
    # synthetic: raw scores correlated with outcome -> calibration lowers Brier
    import random
    random.seed(0)
    sc = [random.gauss(0, 1) for _ in range(300)]
    out = [1 if (s + random.gauss(0, 1)) > 0 else 0 for s in sc]
    pl = platt_fit(sc, out); iso = isotonic_fit(sc, out)
    print("platt:", pl)
    print("isotonic pts:", len(iso["x"]))
    print("apply platt(1.0):", round(platt_apply(pl, 1.0), 3),
          "isotonic(1.0):", round(isotonic_apply(iso, 1.0), 3))
