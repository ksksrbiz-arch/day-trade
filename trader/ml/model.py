"""Pure-NumPy logistic regression with standardization, L2, and class
balancing. Probabilistic output is naturally calibrated (sigmoid); standardized
coefficients double as interpretable feature importances. Persists to JSON so
the 24/7 retrain daemon has zero binary/ABI fragility.
"""
from __future__ import annotations

import json
import os
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "ml"))
MODEL_PATH = os.environ.get("ML_MODEL_PATH", os.path.join(_DATA, "model.json"))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class LogisticModel:
    def __init__(self, names):
        self.names = list(names)
        self.w = np.zeros(len(names))
        self.b = 0.0
        self.mean = np.zeros(len(names))
        self.std = np.ones(len(names))
        self.meta: dict = {}

    # ---- training -------------------------------------------------------- #
    def fit(self, X, y, l2=1.0, lr=0.2, epochs=400, balance=True):
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0)
        self.std[self.std == 0] = 1.0
        Xs = (X - self.mean) / self.std
        n, d = Xs.shape
        self.w = np.zeros(d)
        self.b = 0.0
        # class weights to counter imbalance
        if balance:
            pos = max(1, int(y.sum())); neg = max(1, n - pos)
            wpos, wneg = n / (2 * pos), n / (2 * neg)
            sw = np.where(y == 1, wpos, wneg)
        else:
            sw = np.ones(n)
        sw = sw / sw.mean()
        for _ in range(epochs):
            p = _sigmoid(Xs @ self.w + self.b)
            g = (p - y) * sw
            gw = Xs.T @ g / n + l2 * self.w / n
            gb = g.mean()
            self.w -= lr * gw
            self.b -= lr * gb
        return self

    # ---- inference ------------------------------------------------------- #
    def proba(self, X):
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X[None, :]
        Xs = (X - self.mean) / self.std
        return _sigmoid(Xs @ self.w + self.b)

    def score_one(self, vec) -> float:
        """ml_score in [-1,1] = 2*P(up) - 1."""
        return float(2 * self.proba(np.asarray(vec, dtype=float))[0] - 1)

    def importances(self) -> dict:
        a = np.abs(self.w)
        tot = a.sum() or 1.0
        return dict(sorted(((n, round(float(v), 4)) for n, v in zip(self.names, a / tot)),
                           key=lambda kv: kv[1], reverse=True))

    # ---- persistence ----------------------------------------------------- #
    def to_dict(self) -> dict:
        return {"names": self.names, "w": self.w.tolist(), "b": self.b,
                "mean": self.mean.tolist(), "std": self.std.tolist(),
                "meta": self.meta}

    @classmethod
    def from_dict(cls, d) -> "LogisticModel":
        m = cls(d["names"])
        m.w = np.asarray(d["w"]); m.b = float(d["b"])
        m.mean = np.asarray(d["mean"]); m.std = np.asarray(d["std"])
        m.meta = d.get("meta", {})
        return m

    def save(self, path: str = MODEL_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str = MODEL_PATH):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return cls.from_dict(json.load(f))


# ---- metrics --------------------------------------------------------------- #
def auc(y_true, p) -> float:
    y = np.asarray(y_true); p = np.asarray(p)
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return 0.5
    # Mann-Whitney U with AVERAGE ranks for ties (proper AUC).
    order = np.argsort(p, kind="mergesort")
    sp = p[order]
    ranks = np.empty(len(p), dtype=float)
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and sp[j + 1] == sp[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0   # 1-based average rank
        i = j + 1
    sum_pos = ranks[y == 1].sum()
    return float((sum_pos - npos * (npos + 1) / 2) / (npos * nneg))


def accuracy(y_true, p, thr=0.5) -> float:
    return float((np.asarray(p) >= thr).astype(int).__eq__(np.asarray(y_true)).mean())


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    X = rng.normal(size=(800, 4))
    y = (X[:, 0] + 0.5 * X[:, 1] - X[:, 2] + rng.normal(scale=0.5, size=800) > 0).astype(int)
    m = LogisticModel(["a", "b", "c", "d"]).fit(X, y)
    p = m.proba(X)
    print("train AUC", round(auc(y, p), 3), "acc", round(accuracy(y, p), 3))
    print("importances", m.importances())
