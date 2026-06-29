"""Neural Core (cortex) -- a learned, nonlinear, *ensembled* decision fuser.

The confluence brain blends its voices LINEARLY (a weighted vote). The cortex is
the neural upgrade: a small pure-NumPy multilayer perceptron (TWO hidden ReLU
layers) that learns the NONLINEAR interactions between voices from realized
outcomes -- e.g. "tnet only matters when ml and quant agree", or "council is
noise in high-vol". It maps the per-method score vector to P(up) by
backpropagation on the same resolved-decision dataset the linear learner uses.

Enhancements over a single net:
  * DEEPER -- two hidden layers (d -> h1 -> h2 -> 1) for richer interactions.
  * ENSEMBLE -- K differently-seeded nets; the conviction is their mean and the
    disagreement (std) becomes an honest CONFIDENCE estimate.
  * SALIENCY -- input-gradient attribution shows which voices the core keys on.
  * HARDENED -- NaN/inf-safe inputs, finite-guarded forward, champion/challenger
    promotion (a new ensemble only replaces the live one if val-accuracy holds).

Safe by construction: `conviction()` returns 0 until trained; the core only
influences live trading when explicitly enabled (default off) after proving
itself in the Shadow Lab.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

METHODS = ["ta", "quant", "fundamental", "ml", "council", "prediction", "tnet"]
H1, H2 = 12, 6              # two hidden layers
ENSEMBLE = 5               # number of seeded members
_SEEDS = [17, 29, 43, 61, 83]

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "cortex"))
WEIGHTS = os.path.join(_DATA, "cortex.npz")
CARD = os.path.join(_DATA, "card.json")
ENABLED = os.path.join(_DATA, "enabled.json")

_cache = {"ts": 0.0, "params": None, "loaded": False}


# ----------------------------- flags -------------------------------------- #
def enabled() -> bool:
    """Whether the neural core is allowed to influence LIVE confluence (default off)."""
    try:
        return bool(json.load(open(ENABLED)).get("enabled", False))
    except Exception:  # noqa: BLE001
        return False


def set_enabled(on: bool) -> dict:
    os.makedirs(_DATA, exist_ok=True)
    json.dump({"enabled": bool(on)}, open(ENABLED, "w"))
    return {"enabled": bool(on)}


# ----------------------------- math --------------------------------------- #
def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


def _relu(z):
    return np.maximum(0.0, z)


def _clean(X):
    """Harden inputs: replace NaN/inf, clip extremes."""
    return np.clip(np.nan_to_num(np.asarray(X, dtype=float), nan=0.0, posinf=4.0, neginf=-4.0), -4.0, 4.0)


def _init(d, h1, h2, seed):
    g = np.random.default_rng(seed)
    return {"W1": g.standard_normal((d, h1)) * np.sqrt(2.0 / d), "b1": np.zeros(h1),
            "W2": g.standard_normal((h1, h2)) * np.sqrt(2.0 / h1), "b2": np.zeros(h2),
            "W3": g.standard_normal((h2, 1)) * np.sqrt(2.0 / h2), "b3": np.zeros(1)}


def _fwd(X, p):
    z1 = X @ p["W1"] + p["b1"]; a1 = _relu(z1)
    z2 = a1 @ p["W2"] + p["b2"]; a2 = _relu(z2)
    z3 = a2 @ p["W3"] + p["b3"]
    out = _sigmoid(z3).ravel()
    return out, (z1, a1, z2, a2)


def _saliency(X, p):
    """d(out)/d(input) via analytic backprop -- magnitude per input voice."""
    out, (z1, a1, z2, a2) = _fwd(X, p)
    d3 = (out * (1 - out)).reshape(-1, 1)         # (n,1)
    d2 = (d3 @ p["W3"].T) * (z2 > 0)              # (n,h2)
    d1 = (d2 @ p["W2"].T) * (z1 > 0)              # (n,h1)
    dx = d1 @ p["W1"].T                           # (n,d)
    return np.abs(dx).mean(axis=0)               # (d,)


# ----------------------------- persistence -------------------------------- #
def _load():
    now = time.time()
    if _cache["loaded"] and now - _cache["ts"] < 60:
        return _cache["params"]
    members = None
    if os.path.exists(WEIGHTS):
        try:
            d = np.load(WEIGHTS)
            k = int(d["K"])
            members = [{n: d[f"n{i}_{n}"] for n in ("W1", "b1", "W2", "b2", "W3", "b3")}
                       for i in range(k)]
        except Exception:  # noqa: BLE001
            members = None
    _cache.update(ts=now, params=members, loaded=True)
    return members


def _save(members, meta: dict):
    os.makedirs(_DATA, exist_ok=True)
    flat = {"K": len(members)}
    for i, p in enumerate(members):
        for n, arr in p.items():
            flat[f"n{i}_{n}"] = arr
    np.savez(WEIGHTS, **flat)
    with open(CARD, "w") as f:
        json.dump(meta, f, indent=2)
    _cache.update(loaded=False)


# ----------------------------- training ----------------------------------- #
def _fit(X, y, seed, epochs=1600, lr=0.05, l2=1e-4):
    n, d = X.shape
    p = _init(d, H1, H2, seed)
    yv = y.reshape(-1, 1)
    for _ in range(epochs):
        z1 = X @ p["W1"] + p["b1"]; a1 = _relu(z1)
        z2 = a1 @ p["W2"] + p["b2"]; a2 = _relu(z2)
        z3 = a2 @ p["W3"] + p["b3"]; out = _sigmoid(z3)
        d3 = (out - yv) / n
        dW3 = a2.T @ d3 + l2 * p["W3"]; db3 = d3.sum(axis=0)
        d2 = (d3 @ p["W3"].T) * (z2 > 0)
        dW2 = a1.T @ d2 + l2 * p["W2"]; db2 = d2.sum(axis=0)
        d1 = (d2 @ p["W2"].T) * (z1 > 0)
        dW1 = X.T @ d1 + l2 * p["W1"]; db1 = d1.sum(axis=0)
        p["W3"] -= lr * dW3; p["b3"] -= lr * db3
        p["W2"] -= lr * dW2; p["b2"] -= lr * db2
        p["W1"] -= lr * dW1; p["b1"] -= lr * db1
    return p


def train(min_samples: int = 30) -> dict:
    """Train a K-net ensemble on resolved decisions; champion/challenger on val acc."""
    try:
        from . import backprop
        X, y = backprop.build_dataset()
    except Exception as e:  # noqa: BLE001
        return {"trained": False, "reason": f"dataset error: {str(e)[:80]}"}
    X = _clean(X)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(y)
    X, y = X[mask], y[mask]
    if X.shape[0] < min_samples:
        return {"trained": False, "reason": f"only {X.shape[0]} samples (need {min_samples})"}
    rng = np.random.default_rng(0)
    idx = rng.permutation(X.shape[0])
    X, y = X[idx], y[idx]
    cut = max(1, int(0.8 * X.shape[0]))
    Xtr, ytr, Xva, yva = X[:cut], y[:cut], X[cut:], y[cut:]
    if len(Xva) < 3:
        Xva, yva = Xtr, ytr

    members = [_fit(Xtr, ytr, s) for s in _SEEDS[:ENSEMBLE]]
    pv = np.mean([_fwd(Xva, p)[0] for p in members], axis=0)          # ensemble mean
    val_acc = float(np.mean((pv >= 0.5) == (yva >= 0.5)))
    ptr = np.mean([_fwd(Xtr, p)[0] for p in members], axis=0)
    bce = float(-np.mean(ytr * np.log(ptr + 1e-9) + (1 - ytr) * np.log(1 - ptr + 1e-9)))

    prev = card()
    champ = prev.get("val_acc", -1.0) if prev.get("trained") else -1.0
    promoted = val_acc >= champ
    meta = {"trained": True, "val_acc": round(val_acc, 3), "loss": round(bce, 4),
            "arch": [int(X.shape[1]), H1, H2], "members": len(members),
            "n": int(X.shape[0]), "features": len(METHODS),
            "promoted": bool(promoted), "champion_val_acc": round(max(val_acc, champ), 3),
            "updated": time.strftime("%Y-%m-%d %H:%M")}
    if promoted:
        _save(members, meta)
    return meta


# ----------------------------- serving ------------------------------------ #
def conviction(scores: dict) -> dict:
    """Ensemble nonlinear conviction in [-1,1] + confidence + per-voice saliency."""
    members = _load()
    if not members:
        return {"trained": False, "conviction": 0.0, "p_up": 0.5, "confidence": 0.0}
    x = _clean([[scores.get(m, 0.0) or 0.0 for m in METHODS]])
    ps = np.array([_fwd(x, p)[0][0] for p in members])
    pu = float(ps.mean())
    conf = float(np.clip(1.0 - 2.0 * ps.std(), 0.0, 1.0))     # ensemble agreement
    sal = np.mean([_saliency(x, p) for p in members], axis=0)
    tot = sal.sum() or 1.0
    importance = {METHODS[i]: round(float(sal[i] / tot), 3) for i in range(len(METHODS))}
    return {"trained": True, "conviction": round(2 * pu - 1, 4), "p_up": round(pu, 4),
            "confidence": round(conf, 3), "saliency": importance}


def card() -> dict:
    if not os.path.exists(CARD):
        return {"trained": False}
    try:
        return json.load(open(CARD))
    except Exception:  # noqa: BLE001
        return {"trained": False}


# ----------------------------- telemetry ---------------------------------- #
HIST = os.path.join(_DATA, "history.jsonl")
_MAXH = 500


def log_live(scores: dict, conv: dict) -> None:
    """Append a live conviction call so the dashboard can sparkline its mind."""
    try:
        os.makedirs(_DATA, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "p_up": conv.get("p_up"), "conviction": conv.get("conviction"),
               "confidence": conv.get("confidence"), "saliency": conv.get("saliency")}
        # idempotent-ish: skip if identical to last line (avoids cron dupes)
        last = None
        if os.path.exists(HIST):
            with open(HIST) as f:
                lines = f.readlines()[-1:]
                if lines:
                    try:
                        last = json.loads(lines[0])
                    except Exception:  # noqa: BLE001
                        last = None
        if last and last.get("p_up") == rec["p_up"] and last.get("conviction") == rec["conviction"]:
            return
        with open(HIST, "a") as f:
            f.write(json.dumps(rec) + "\n")
        # cap file
        if os.path.exists(HIST):
            with open(HIST) as f:
                lines = f.readlines()
            if len(lines) > _MAXH:
                with open(HIST, "w") as f:
                    f.writelines(lines[-_MAXH:])
    except Exception:  # noqa: BLE001
        pass


def history(limit: int = 120) -> list:
    try:
        with open(HIST) as f:
            lines = f.readlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:  # noqa: BLE001
        return []


def calibration() -> dict:
    """Reliability of the trained ensemble on the resolved-decision dataset:
    bin predicted P(up) into deciles and compare to realized up-rate. Also
    returns accuracy and Brier score -- an honest 'how well-calibrated am I'."""
    members = _load()
    if not members:
        return {"trained": False}
    try:
        from . import backprop
        X, y = backprop.build_dataset()
    except Exception as e:  # noqa: BLE001
        return {"trained": True, "error": f"dataset: {str(e)[:60]}"}
    X = _clean(X); y = np.asarray(y, dtype=float)
    m = np.isfinite(y); X, y = X[m], y[m]
    if X.shape[0] < 8:
        return {"trained": True, "n": int(X.shape[0]), "insufficient": True}
    p = np.mean([_fwd(X, mp)[0] for mp in members], axis=0)
    acc = float(np.mean((p >= 0.5) == (y >= 0.5)))
    brier = float(np.mean((p - y) ** 2))
    bins = []
    edges = np.linspace(0, 1, 11)
    for i in range(10):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p < hi) if i < 9 else (p >= lo) & (p <= hi)
        if sel.sum() > 0:
            bins.append({"bin": round((lo + hi) / 2, 2), "pred": round(float(p[sel].mean()), 3),
                         "actual": round(float(y[sel].mean()), 3), "n": int(sel.sum())})
    return {"trained": True, "n": int(X.shape[0]), "accuracy": round(acc, 3),
            "brier": round(brier, 4), "reliability": bins}


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
    print("conviction:", conviction({m: 0.5 for m in METHODS}))
