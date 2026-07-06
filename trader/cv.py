"""
Validation rigor: purged/embargoed cross-validation + deflated Sharpe ratio.

Two honest-measurement tools, both pure NumPy/stdlib:

1. PURGED, EMBARGOED K-FOLD (Lopez de Prado, "Advances in Financial ML").
   Our labels are forward `horizon`-day returns, so a naive train/test split
   leaks: training samples whose label window overlaps the test period share
   information with it, inflating the score. purged_kfold() drops (purges) any
   training sample whose [t, t+horizon] window overlaps a test fold, plus an
   EMBARGO gap after each test fold. The mean out-of-fold score is an honest,
   lower-variance estimate of real generalization.

2. DEFLATED SHARPE RATIO (Bailey & Lopez de Prado, 2014). When you try N
   strategies and keep the best, its Sharpe is upward-biased by selection. The
   DSR is the probability the TRUE Sharpe > 0 after deflating for (a) the number
   of trials, (b) non-normal returns (skew/kurtosis), and (c) sample length. A
   "winner" that doesn't clear DSR > ~0.95 is probably an overfit artifact.
"""
from __future__ import annotations

import math

import numpy as np


# ----------------------------- normal helpers ----------------------------- #
def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation)."""
    p = min(1 - 1e-12, max(1e-12, p))
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ----------------------------- deflated Sharpe ---------------------------- #
def sharpe(returns) -> float:
    r = np.asarray(returns, dtype=float)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1))


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """E[max Sharpe] under the null across N independent trials (Bailey/LdP)."""
    if n_trials < 2 or sr_std <= 0:
        return 0.0
    g = 0.5772156649015329                      # Euler-Mascheroni
    z1 = _norm_ppf(1 - 1.0 / n_trials)
    z2 = _norm_ppf(1 - 1.0 / (n_trials * math.e))
    return sr_std * ((1 - g) * z1 + g * z2)


def deflated_sharpe(returns, n_trials: int = 1, sr_trials_std: float | None = None) -> dict:
    """Probability the true (per-observation) Sharpe > benchmark after deflating
    for N trials + non-normality + sample size. DSR in [0,1]; >0.95 ~= real."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 10:
        return {"dsr": 0.0, "sr": 0.0, "sr0": 0.0, "n": n, "note": "too few"}
    sr = sharpe(r)
    sd = r.std(ddof=1)
    skew = float(((r - r.mean()) ** 3).mean() / sd ** 3) if sd > 0 else 0.0
    kurt = float(((r - r.mean()) ** 4).mean() / sd ** 4) if sd > 0 else 3.0
    # benchmark = expected max Sharpe under the null across the trials
    sr_std = sr_trials_std if sr_trials_std is not None else (abs(sr) / math.sqrt(max(1, n)) + 1e-9)
    sr0 = expected_max_sharpe(n_trials, sr_std) if n_trials > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2))
    z = (sr - sr0) * math.sqrt(n - 1) / denom
    return {"dsr": round(_norm_cdf(z), 4), "sr": round(sr, 4), "sr0": round(sr0, 4),
            "skew": round(skew, 3), "kurt": round(kurt, 3), "n": n, "n_trials": n_trials}


def deflated_sharpe_from_stats(sr: float, n: int, n_trials: int = 1,
                              sr_trials_std: float | None = None) -> dict:
    """DSR from summary stats (normal assumption) -- for ranking many strategies
    where you have each one's Sharpe but not its full return series."""
    if n < 10:
        return {"dsr": 0.0, "sr": round(sr, 4), "sr0": 0.0, "n": n}
    sr_std = sr_trials_std if sr_trials_std is not None else (abs(sr) / math.sqrt(max(1, n)) + 1e-9)
    sr0 = expected_max_sharpe(n_trials, sr_std) if n_trials > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1.0 + 0.5 * sr ** 2))       # skew=0, kurt=3
    z = (sr - sr0) * math.sqrt(n - 1) / denom
    return {"dsr": round(_norm_cdf(z), 4), "sr": round(sr, 4),
            "sr0": round(sr0, 4), "n": n, "n_trials": n_trials}


# ----------------------------- purged K-fold ------------------------------ #
def purged_kfold(dates, horizon: int, k: int = 5, embargo_frac: float = 0.01):
    """Yield (train_idx, test_idx) for time-ordered samples, purging training
    rows whose label window overlaps the test fold + an embargo after it."""
    n = len(dates)
    if n < k * 3:
        return
    order = np.argsort(np.asarray(dates))
    folds = np.array_split(order, k)
    embargo = int(n * embargo_frac)
    for i in range(k):
        test = folds[i]
        t0, t1 = int(test.min()), int(test.max())      # positions in the sorted order
        # map: keep a train sample only if its [pos, pos+horizon] cannot see the test span
        train = []
        pos_of = {int(idx): p for p, idx in enumerate(order)}
        for idx in order:
            p = pos_of[int(idx)]
            if t0 <= p <= t1:
                continue                                # in test
            # purge: label window [p, p+horizon] overlapping test, or within embargo after
            if p < t0 and p + horizon >= t0:
                continue
            if p > t1 and p <= t1 + embargo:
                continue
            train.append(int(idx))
        if train and len(test):
            yield np.asarray(train), np.asarray(test)


def cv_auc(X, y, dates, horizon: int, fit_fn, score_fn, k: int = 5) -> dict:
    """Mean out-of-fold AUC via purged K-fold. fit_fn(Xtr,ytr)->model;
    score_fn(model,Xte)->proba. Honest generalization estimate."""
    X = np.asarray(X); y = np.asarray(y)
    aucs = []
    from .ml.model import auc as _auc
    for tr, te in purged_kfold(dates, horizon, k=k):
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        try:
            m = fit_fn(X[tr], y[tr])
            p = score_fn(m, X[te])
            aucs.append(_auc(y[te], p))
        except Exception:  # noqa: BLE001
            continue
    if not aucs:
        return {"cv_auc": 0.5, "cv_std": 0.0, "folds": 0}
    return {"cv_auc": round(float(np.mean(aucs)), 4),
            "cv_std": round(float(np.std(aucs)), 4), "folds": len(aucs),
            "cv_auc_lo": round(float(np.mean(aucs) - np.std(aucs)), 4)}


if __name__ == "__main__":
    import random
    random.seed(0)
    # a genuinely weak edge sampled 200x -> the best looks great, DSR deflates it
    best = max(([random.gauss(0.0003, 0.01) for _ in range(250)] for _ in range(200)),
               key=lambda r: sharpe(r))
    print("naive Sharpe:", round(sharpe(best), 3))
    print("deflated:", deflated_sharpe(best, n_trials=200))
