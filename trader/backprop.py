"""Backpropagation over the confluence layer.

The confluence blender IS a single-layer neural net: its inputs are the method
"neurons" (ta, quant, fundamental, ml, council, prediction), its parameters are
the per-method weights, its output is P(up), and the realized trade outcome is
the label. This module trains those weights with real backprop:

  forward:   z = X·w + b ;  p = sigmoid(z)          (sigmoid output activation)
  loss:      cross-entropy  CE = -mean[ y·log p + (1-y)·log(1-p) ]
  backward:  dL/dw = Xᵀ(p - y)/n + l2·w ;  dL/db = mean(p - y)
  descent:   w -= lr·dL/dw ;  b -= lr·dL/db          (gradient descent)

The learned weights are turned into a confluence emphasis via ReLU (drop methods
that aren't positively predictive) + softmax (normalize to an attention-like
distribution) and fed back so the blend is LEARNED, not hand-set. Pure NumPy.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import numpy as np

METHODS = ["ta", "quant", "fundamental", "ml", "council", "prediction", "tnet", "alpha_engine"]
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "backprop"))
DECISIONS = os.path.join(_DATA, "decisions.jsonl")
WEIGHTS = os.path.join(_DATA, "weights.json")
HISTORY = os.path.join(_DATA, "history.json")
MIN_SAMPLES = 30


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- decision logging (the forward-pass inputs captured live) -------------- #
def log_decision(symbol, scores: dict, day=None, ref_price=None, horizon=5, asset="equity"):
    """Record the method-score vector at decision time (idempotent per sym/day)."""
    os.makedirs(_DATA, exist_ok=True)
    day = day or time.strftime("%Y-%m-%d", time.gmtime())
    did = hashlib.sha1(f"{symbol}|{day}".encode()).hexdigest()[:16]
    # de-dup
    if os.path.exists(DECISIONS):
        for ln in open(DECISIONS, encoding="utf-8"):
            if f'"id": "{did}"' in ln:
                return False
    vec = {m: float(scores.get(m, 0.0) or 0.0) for m in METHODS}
    rec = {"id": did, "ts": _now(), "day": day, "symbol": symbol.upper(),
           "asset": asset, "horizon": int(horizon),
           "ref_price": float(ref_price) if ref_price else None, "scores": vec}
    with open(DECISIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return True


def _price_fn(sym, asset, day, horizon):
    try:
        if asset == "crypto":
            from . import history
            p = history.load_panel([sym], days=400, source="coinex")
            ser = list(zip(p["dates"], p["prices"].get(sym, [])))
        else:
            # Alpaca IEX daily bars are the source the decision store is logged
            # from, so resolve against the SAME series first (cloud CRSP is empty
            # or stale and would not cover the backfilled dates). CRSP is only a
            # local fallback when Alpaca keys are absent.
            from .ml.dataset import _alpaca_series
            ser = _alpaca_series(sym)
            if len(ser) < 30:
                from .crsp import query as crsp
                ser = [(b["date"], b["close"]) for b in crsp.get_prices(sym, "2024-01-01", None) if b.get("close")]
    except Exception:  # noqa: BLE001
        return None
    if not ser:
        return None
    ds = [d for d, _ in ser]; cl = [c for _, c in ser]
    idx = next((i for i, d in enumerate(ds) if d >= day), None)
    if idx is None or idx + horizon >= len(cl):
        return None
    return cl[idx], cl[idx + horizon]


_DS_CACHE: dict = {"key": None, "at": 0.0, "X": None, "y": None}
_DS_TTL = 600.0                 # re-resolve at most every 10 min (bars mature slowly)


def build_dataset():
    """Resolve logged decisions into (X method-vectors, y up/down).

    Memoized on the decision-log signature (mtime+size) with a 10-min TTL: the
    cortex, confluence-backprop, calibrator, attribution and the autonomy sweep
    (3x/cycle via _cortex_samples) all call this, and resolving ~1.5k rows every
    time is wasted work when the log hasn't changed. Resolved labels are stable
    (only decisions older than the horizon resolve), so caching is safe."""
    import time as _t
    if not os.path.exists(DECISIONS):
        return np.zeros((0, len(METHODS))), np.zeros(0)
    try:
        st = os.stat(DECISIONS)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if (key is not None and _DS_CACHE["key"] == key
            and _DS_CACHE["X"] is not None and (_t.time() - _DS_CACHE["at"]) < _DS_TTL):
        return _DS_CACHE["X"], _DS_CACHE["y"]
    X, y = [], []
    for ln in open(DECISIONS, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        res = _price_fn(r["symbol"], r.get("asset", "equity"), r["day"], r.get("horizon", 5))
        if not res:
            continue
        p0, p1 = res
        if not p0:
            continue
        X.append([r["scores"].get(m, 0.0) for m in METHODS])
        y.append(1 if (p1 / p0 - 1.0) > 0 else 0)
    Xa = np.asarray(X, dtype=float); ya = np.asarray(y, dtype=float)
    _DS_CACHE.update(key=key, at=_t.time(), X=Xa, y=ya)
    return Xa, ya


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def train(epochs=600, lr=0.3, l2=0.5) -> dict:
    """Backprop the confluence weights. Returns metrics; saves weights on success."""
    X, y = build_dataset()
    n = len(X)
    if n < MIN_SAMPLES:
        return {"ok": False, "reason": f"need {MIN_SAMPLES}+ resolved decisions, have {n}"}
    d = X.shape[1]
    w = np.zeros(d); b = 0.0
    pos = max(1, int(y.sum())); neg = max(1, n - pos)
    sw = np.where(y == 1, n / (2 * pos), n / (2 * neg)); sw /= sw.mean()
    loss0 = None
    for _ in range(epochs):
        p = _sigmoid(X @ w + b)                       # forward
        g = (p - y) * sw                              # dL/dz (cross-entropy + sigmoid)
        gw = X.T @ g / n + l2 * w / n                 # backward
        gb = g.mean()
        w -= lr * gw; b -= lr * gb                    # gradient descent
        if loss0 is None:
            eps = 1e-9
            loss0 = float(-np.mean(sw * (y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))))
    p = _sigmoid(X @ w + b)
    eps = 1e-9
    loss = float(-np.mean(sw * (y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))))
    acc = float(((p >= 0.5).astype(int) == y).mean())

    # ReLU emphasis (drop anti-predictive methods) -> softmax (attention-like)
    relu = np.maximum(0.0, w)
    if relu.sum() == 0:
        emphasis = np.ones(d) / d
    else:
        e = np.exp(relu - relu.max())
        emphasis = e / e.sum()
    out = {
        "ok": True, "n": n, "loss": round(loss, 4), "loss_start": round(loss0 or 0, 4),
        "accuracy": round(acc, 4), "base_rate": round(float(y.mean()), 4),
        "weights": {m: round(float(w[i]), 4) for i, m in enumerate(METHODS)},
        "emphasis": {m: round(float(emphasis[i]), 4) for i, m in enumerate(METHODS)},
        "bias": round(float(b), 4), "trained_at": _now(),
    }
    os.makedirs(_DATA, exist_ok=True)
    json.dump(out, open(WEIGHTS, "w"), indent=2)
    hist = []
    if os.path.exists(HISTORY):
        try:
            hist = json.load(open(HISTORY))
        except Exception:  # noqa: BLE001
            hist = []
    hist.append({k: out[k] for k in ("trained_at", "n", "loss", "accuracy")})
    json.dump(hist[-200:], open(HISTORY, "w"), indent=2)
    # tell the mesh / brain a learning step happened
    try:
        from . import mesh
        mesh.publish("ml", "backprop",
                     f"backprop updated confluence weights: loss {out['loss_start']}→{out['loss']}, "
                     f"acc {acc:.0%}, n={n}", salience=0.7)
    except Exception:  # noqa: BLE001
        pass
    return out


def learned_emphasis() -> dict | None:
    """Per-method emphasis weights for confluence (softmax of ReLU'd learned w)."""
    if not os.path.exists(WEIGHTS):
        return None
    try:
        d = json.load(open(WEIGHTS))
        return d.get("emphasis")
    except Exception:  # noqa: BLE001
        return None


def card() -> dict:
    if not os.path.exists(WEIGHTS):
        return {"trained": False}
    try:
        d = json.load(open(WEIGHTS)); d["trained"] = True
        return d
    except Exception:  # noqa: BLE001
        return {"trained": False}


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
