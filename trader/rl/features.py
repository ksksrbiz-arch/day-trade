"""Pure-NumPy observation features for the RL trader.

This module has ZERO heavy dependencies (numpy only) on purpose: it is the one
piece of the RL stack that the lean core can import and unit-test without
TensorFlow/TensorTrade present. The env builder (env.py) turns each column here
into a TensorTrade `Stream`; the live `decide()` path reuses the exact same
function to build the final observation window, so training and inference see
identically-constructed inputs.

Given a close-price series it returns an (N, F) matrix of bounded, roughly
zero-centred features -- log returns, SMA distance, rolling vol, RSI, momentum.
Everything is causal (uses only past/current bars) so there is no lookahead.
"""
from __future__ import annotations

import numpy as np

FEATURE_NAMES = ("lret", "sma5_dist", "sma20_dist", "vol10", "rsi14", "mom10")


def _sma(x: np.ndarray, w: int) -> np.ndarray:
    """Causal simple moving average; the first `w-1` points use the running mean."""
    out = np.empty_like(x, dtype=float)
    csum = np.cumsum(x, dtype=float)
    for i in range(len(x)):
        if i < w:
            out[i] = csum[i] / (i + 1)
        else:
            out[i] = (csum[i] - csum[i - w]) / w
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder-style RSI mapped to [-1, 1]; warmup uses a shorter window."""
    delta = np.diff(closes, prepend=closes[0])
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    avg_g = _sma(gain, period)
    avg_l = _sma(loss, period)
    rs = avg_g / np.where(avg_l == 0, 1e-9, avg_l)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return (rsi - 50.0) / 50.0


def build_features(closes) -> tuple[np.ndarray, tuple[str, ...]]:
    """Return (matrix (N, F), FEATURE_NAMES) for a close-price series.

    Raises ValueError if the series is too short to be meaningful.
    """
    c = np.asarray([float(x) for x in closes], dtype=float)
    if c.ndim != 1 or len(c) < 3:
        raise ValueError(f"need >=3 closes, got {len(c)}")
    c = np.where(c <= 0, np.nan, c)
    # forward-fill any non-positive/NaN prints so log/ratios stay finite
    for i in range(len(c)):
        if not np.isfinite(c[i]):
            c[i] = c[i - 1] if i > 0 else 1.0

    lc = np.log(c)
    lret = np.diff(lc, prepend=lc[0])
    sma5 = _sma(c, 5)
    sma20 = _sma(c, 20)
    sma5_dist = c / sma5 - 1.0
    sma20_dist = c / sma20 - 1.0

    # rolling std of log returns (causal, window 10)
    vol10 = np.empty_like(c)
    for i in range(len(c)):
        lo = max(0, i - 9)
        seg = lret[lo:i + 1]
        vol10[i] = seg.std() if len(seg) > 1 else 0.0

    rsi14 = _rsi(c, 14)

    mom10 = np.empty_like(c)
    for i in range(len(c)):
        j = max(0, i - 10)
        mom10[i] = lc[i] - lc[j]

    mat = np.column_stack([lret, sma5_dist, sma20_dist, vol10, rsi14, mom10])
    mat = np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    return mat, FEATURE_NAMES


def latest_window(closes, window: int) -> np.ndarray:
    """Build the most-recent (window, F) observation for live inference.

    Left-pads with the earliest available row when history is shorter than
    `window`, so `decide()` never crashes on a thin series.
    """
    mat, _ = build_features(closes)
    if len(mat) >= window:
        return mat[-window:]
    pad = np.repeat(mat[:1], window - len(mat), axis=0)
    return np.vstack([pad, mat])
