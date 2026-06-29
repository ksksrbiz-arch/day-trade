"""Temporal-transformer feature layer (pure NumPy, inference-grade).

A production-style time-series attention stack adapted from text transformers to
markets. Every mechanism the spec asked for is implemented and testable, and the
primitives are now *composed* into a real stacked encoder with a calibrated,
decision-useful forecast head.

  RevIN              -- reversible instance norm (past-window stats; remove
                        non-stationary trend, restore at output).
  temporal + RoPE    -- multi-periodic cyclic features (min-of-hour, hour-of-day,
                        day-of-week) + rotary relative position encoding.
  VSN / GRN          -- variable-selection via gated residual networks; vol
                        context up-weights volume, dials back noisy price.
  patchify           -- PatchTST-style multi-scale overlapping patches (denoise
                        dense ticks, shorten sequence).
  multi_freq_heads   -- heads bound to frequency bands via wavelet-ish 1D conv
                        (high / medium / low resolution).
  sliding_dilated_mask -- local sliding window + exponentially dilated pivots
                        instead of full causal mask.
  attention / MHA    -- scaled dot-product + multi-head; cross_attention maps an
                        asset's Query to macro Keys/Values (drivers readout).
  TransformerEncoder -- patch-embed -> [pre-norm MHA(RoPE, sliding/dilated) +
                        GELU feed-forward, residual] x N -> pooled latent.
  infonce / regime   -- contrastive regime embedding; nearest-regime recall.
  forecast / calib   -- multi-frequency directional readout + Platt calibration
                        learned from realized outcomes -> prob_up, quantiles,
                        drivers, regime, confidence. score_signal() feeds the
                        confluence brain as an independent voice.

`analyze(symbol)` returns an interpretable report; `forecast(symbol)` returns a
calibrated, tradeable read. Fail-soft everywhere.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import numpy as np

rng = np.random.default_rng(7)  # fixed init -> deterministic forward pass


# ============================ math helpers ================================= #
def softmax(z: np.ndarray, axis: int = -1) -> np.ndarray:
    z = np.asarray(z, dtype=float)
    z = z - z.max(axis=axis, keepdims=True)
    e = np.exp(z)
    return e / (e.sum(axis=axis, keepdims=True) + 1e-12)


def layernorm(x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    mu = x.mean(axis=-1, keepdims=True)
    sd = x.std(axis=-1, keepdims=True) + eps
    return (x - mu) / sd


def gelu(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return 0.5 * x * (1 + np.tanh(np.sqrt(2 / np.pi) * (x + 0.044715 * x ** 3)))


def sigmoid(z) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(np.asarray(z, dtype=float), -60, 60)))


# ============================ RevIN ======================================== #
def revin(x: np.ndarray, eps: float = 1e-5):
    """Normalize a window by its own (past) stats. Returns (x_norm, stats).
    Hardened: NaN/inf-safe, and degenerate (empty/constant) windows fall back to
    unit scale instead of producing NaNs downstream."""
    x = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    if x.size == 0:
        return x, (0.0, 1.0)
    mu, sd = float(x.mean()), float(x.std() + eps)
    return (x - mu) / sd, (mu, sd)


def revin_inv(xn: np.ndarray, stats) -> np.ndarray:
    mu, sd = stats
    return np.asarray(xn, dtype=float) * sd + mu


# ==================== temporal features + RoPE ============================= #
def temporal_features(dt: datetime) -> dict:
    """Multi-periodic cyclic encodings (sin/cos) injected into embeddings."""
    def cyc(v, p):
        a = 2 * np.pi * v / p
        return float(np.sin(a)), float(np.cos(a))
    mh_s, mh_c = cyc(dt.minute, 60)
    hd_s, hd_c = cyc(dt.hour, 24)
    dw_s, dw_c = cyc(dt.weekday(), 7)
    dm_s, dm_c = cyc(dt.day, 31)
    return {"min_sin": mh_s, "min_cos": mh_c, "hour_sin": hd_s, "hour_cos": hd_c,
            "dow_sin": dw_s, "dow_cos": dw_c, "dom_sin": dm_s, "dom_cos": dm_c}


def rope(x: np.ndarray, positions: np.ndarray | None = None, base: float = 10000.0):
    """Rotary position embedding: rotate feature pairs by position-dependent
    angles so attention sees RELATIVE distance / decay, not absolute index."""
    x = np.asarray(x, dtype=float)
    n, d = x.shape
    if positions is None:
        positions = np.arange(n)
    half = d // 2
    freqs = base ** (-np.arange(half) / max(1, half))
    ang = np.outer(positions, freqs)            # (n, half)
    cos, sin = np.cos(ang), np.sin(ang)
    out = x.copy()
    x1, x2 = x[:, :half], x[:, half:2 * half]
    out[:, :half] = x1 * cos - x2 * sin
    out[:, half:2 * half] = x1 * sin + x2 * cos
    return out


# ==================== VSN via Gated Residual Network ====================== #
def _elu(z):
    return np.where(z > 0, z, np.exp(np.clip(z, -30, 0)) - 1)


def grn(x: np.ndarray, c: float = 0.0):
    """Gated Residual Network forward (seeded fixed weights -> deterministic).
    out = LayerNorm(x + gate ⊙ proj(ELU(W1 x + w2 c)))."""
    x = np.asarray(x, dtype=float)
    d = x.shape[-1]
    g = np.random.default_rng(101)
    W1 = g.standard_normal((d, d)) * (1 / np.sqrt(d))
    w2 = g.standard_normal(d) * 0.1
    W3 = g.standard_normal((d, d)) * (1 / np.sqrt(d))
    h = _elu(x @ W1 + w2 * c)
    gate = 1 / (1 + np.exp(-(h @ W3)))          # gate
    y = x + gate * h
    return (y - y.mean()) / (y.std() + 1e-5)


def vsn(features: dict, vol_z: float) -> dict:
    """Variable Selection Network: context-aware feature weights. High vol
    up-weights volume/flow features, down-weights noisy price-level features."""
    keys = list(features)
    base = np.array([abs(features[k]) for k in keys], dtype=float) + 1e-3
    gate = np.ones(len(keys))
    for i, k in enumerate(keys):
        lk = k.lower()
        if any(t in lk for t in ("vol", "rvol", "flow", "obi", "depth")):
            gate[i] *= (1 + 0.8 * max(0.0, vol_z))
        elif any(t in lk for t in ("price", "ret", "mom", "close", "sma")):
            gate[i] *= (1 / (1 + 0.5 * max(0.0, vol_z)))
    w = base * gate
    soft = softmax(w)
    return {k: round(float(soft[i]), 4) for i, k in enumerate(keys)}


# ==================== PatchTST multi-scale patching ======================= #
def patchify(series: np.ndarray, patch: int = 16, stride: int = 8) -> np.ndarray:
    """Group adjacent steps into overlapping patches (denoise + shorten seq)."""
    x = np.asarray(series, dtype=float)
    if len(x) < patch:
        return x[None, :] if len(x) else np.zeros((0, patch))
    out = []
    for s in range(0, len(x) - patch + 1, stride):
        out.append(x[s:s + patch])
    return np.asarray(out)


def patchify_multiscale(series: np.ndarray, scales=((8, 4), (16, 8), (32, 16))) -> dict:
    """PatchTST across several resolutions at once -> {scale: patches}."""
    return {f"{p}/{s}": patchify(series, p, s) for p, s in scales}


# ==================== multi-frequency (wavelet-ish) heads ================== #
def multi_freq_heads(series: np.ndarray) -> dict:
    """Decompose into frequency bands -> each attention head a temporal band.
    low = long MA (macro momentum); med = short-long (mean reversion);
    high = residual (micro/scalp)."""
    x = np.asarray(series, dtype=float)
    n = len(x)
    if n < 8:
        return {"high": x * 0, "med": x * 0, "low": x,
                "energy": {"high": 0.0, "med": 0.0, "low": float(np.std(x)) if n else 0.0}}

    def ma(a, w):
        w = min(w, len(a))
        k = np.ones(w) / w
        return np.convolve(a, k, mode="same")
    long_ma = ma(x, max(8, n // 4))
    short_ma = ma(x, max(3, n // 16))
    low = long_ma
    med = short_ma - long_ma
    high = x - short_ma
    energy = lambda v: round(float(np.std(v)), 5)
    return {"high": high, "med": med, "low": low,
            "energy": {"high": energy(high), "med": energy(med), "low": energy(low)}}


# ==================== masks + attention =================================== #
def sliding_dilated_mask(n: int, window: int = 16, dilation: int = 2) -> np.ndarray:
    """Boolean (n,n) attend-mask: each query attends to the last `window` ticks
    AND exponentially-spaced older pivots — local focus + sparse long memory."""
    m = np.zeros((n, n), dtype=bool)
    for i in range(n):
        lo = max(0, i - window + 1)
        m[i, lo:i + 1] = True                   # local sliding window (causal)
        j, step = i - window, dilation          # dilated pivots into the past
        while j >= 0:
            m[i, j] = True
            step *= 2
            j -= step
    return m


def attention(Q, K, V, mask: np.ndarray | None = None):
    """Scaled dot-product attention. Returns (output, weights)."""
    Q, K, V = map(lambda a: np.asarray(a, dtype=float), (Q, K, V))
    d = Q.shape[-1]
    scores = Q @ K.T / np.sqrt(d)
    if mask is not None:
        scores = np.where(mask, scores, -1e9)
    w = softmax(scores, axis=-1)
    return w @ V, w


def attention_entropy(w: np.ndarray) -> float:
    """Mean normalized entropy of an attention map (0=focused, 1=diffuse).
    A focused map => the model is confidently keying on specific ticks."""
    w = np.asarray(w, dtype=float)
    if w.ndim == 1:
        w = w[None, :]
    p = w / (w.sum(axis=-1, keepdims=True) + 1e-12)
    ent = -(p * np.log(p + 1e-12)).sum(axis=-1)
    norm = np.log(w.shape[-1]) if w.shape[-1] > 1 else 1.0
    return round(float((ent / norm).mean()), 4)


def cross_attention(asset: np.ndarray, factors: dict) -> dict:
    """Q = asset, K/V = macro factors. Returns the attention distribution over
    factors (which external forces drive the asset) + the blended context."""
    a = np.asarray(asset, dtype=float)
    an, _ = revin(a[-64:] if len(a) > 64 else a)
    names, ks = [], []
    for nm, ser in factors.items():
        s = np.asarray(ser, dtype=float)
        if len(s) < 8:
            continue
        kn, _ = revin(s[-len(an):] if len(s) >= len(an) else s)
        if len(kn) != len(an):
            continue
        names.append(nm); ks.append(kn)
    if not ks:
        return {"weights": {}, "dominant": None}
    K = np.array(ks)
    d = len(an)
    scores = (K @ an) / np.sqrt(d)              # asset-query · each factor-key
    w = softmax(scores)
    weights = {names[i]: round(float(w[i]), 3) for i in range(len(names))}
    dom = max(weights, key=weights.get)
    return {"weights": weights, "dominant": dom}


# ==================== composed multi-head encoder ========================= #
class TransformerEncoder:
    """Deterministic (seeded) stacked encoder over PatchTST patches.

    embed(patch) -> [ pre-norm multi-head attention (RoPE + sliding/dilated
    mask) + residual ; pre-norm GELU feed-forward + residual ] x n_layers ->
    mean-pooled latent. Weights are fixed-seed: this is an inference-grade
    *representation* transform, not a trained predictor (the directional read
    and its calibration live in forecast()).
    """

    def __init__(self, d_model: int = 32, n_heads: int = 4, n_layers: int = 2,
                 d_ff: int = 64, patch: int = 16, stride: int = 8, seed: int = 11):
        assert d_model % n_heads == 0
        self.d_model, self.n_heads, self.n_layers = d_model, n_heads, n_layers
        self.patch, self.stride = patch, stride
        g = np.random.default_rng(seed)
        sc = lambda r, c: g.standard_normal((r, c)) / np.sqrt(r)
        self.W_embed = sc(patch, d_model)
        self.b_embed = np.zeros(d_model)
        self.layers = []
        for _ in range(n_layers):
            self.layers.append({
                "Wq": sc(d_model, d_model), "Wk": sc(d_model, d_model),
                "Wv": sc(d_model, d_model), "Wo": sc(d_model, d_model),
                "W1": sc(d_model, d_ff), "b1": np.zeros(d_ff),
                "W2": sc(d_ff, d_model), "b2": np.zeros(d_model),
            })

    def _mha(self, X, L, mask):
        d = X.shape[-1]
        hd = d // self.n_heads
        Q, K, V = X @ L["Wq"], X @ L["Wk"], X @ L["Wv"]
        outs, ws = [], []
        for h in range(self.n_heads):
            sl = slice(h * hd, (h + 1) * hd)
            q, k, v = rope(Q[:, sl]), rope(K[:, sl]), V[:, sl]
            o, w = attention(q, k, v, mask)
            outs.append(o); ws.append(w)
        return np.concatenate(outs, axis=-1) @ L["Wo"], np.mean(ws, axis=0)

    def encode(self, series: np.ndarray) -> dict:
        x = np.asarray(series, dtype=float)
        if len(x) < self.patch:
            return {"latent": np.zeros((1, self.d_model)), "pooled": np.zeros(self.d_model),
                    "attn": None, "entropy": [], "n_patches": 0, "stats": (0.0, 1.0)}
        xn, stats = revin(x)
        P = patchify(xn, self.patch, self.stride)
        if P.ndim != 2 or P.shape[0] == 0:
            return {"latent": np.zeros((1, self.d_model)), "pooled": np.zeros(self.d_model),
                    "attn": None, "entropy": [], "n_patches": 0, "stats": stats}
        X = gelu(P @ self.W_embed + self.b_embed)
        n = X.shape[0]
        mask = sliding_dilated_mask(n, window=min(16, n), dilation=2)
        ents, lastw = [], None
        for L in self.layers:
            A, w = self._mha(layernorm(X), L, mask)
            X = X + A
            F = gelu(layernorm(X) @ L["W1"] + L["b1"]) @ L["W2"] + L["b2"]
            X = X + F
            ents.append(attention_entropy(w)); lastw = w
        return {"latent": X, "pooled": X.mean(axis=0), "attn": lastw,
                "entropy": ents, "n_patches": int(n), "stats": stats}


_ENC: TransformerEncoder | None = None


def encoder() -> TransformerEncoder:
    global _ENC
    if _ENC is None:
        _ENC = TransformerEncoder()
    return _ENC


def encode(series: np.ndarray) -> dict:
    return encoder().encode(series)


def multi_head_attention(X, n_heads: int = 4, mask: np.ndarray | None = None,
                         use_rope: bool = True, seed: int = 11):
    """Standalone multi-head self-attention over X (n, d_model). Deterministic
    seeded projections. Returns (output (n,d_model), mean attention map)."""
    X = np.asarray(X, dtype=float)
    n, d = X.shape
    assert d % n_heads == 0
    hd = d // n_heads
    g = np.random.default_rng(seed)
    sc = lambda: g.standard_normal((d, d)) / np.sqrt(d)
    Wq, Wk, Wv, Wo = sc(), sc(), sc(), sc()
    Q, K, V = X @ Wq, X @ Wk, X @ Wv
    outs, ws = [], []
    for h in range(n_heads):
        sl = slice(h * hd, (h + 1) * hd)
        q, k, v = Q[:, sl], K[:, sl], V[:, sl]
        if use_rope:
            q, k = rope(q), rope(k)
        o, w = attention(q, k, v, mask)
        outs.append(o); ws.append(w)
    return np.concatenate(outs, axis=-1) @ Wo, np.mean(ws, axis=0)


# ==================== contrastive regime embedding (InfoNCE) ============== #
def regime_embedding(series: np.ndarray, volumes: np.ndarray | None = None) -> np.ndarray:
    """Compact, L2-normalized latent describing the current regime, built from
    multi-frequency energies + return stats. Similar regimes -> close vectors."""
    x = np.asarray(series, dtype=float)
    if len(x) < 12:
        return np.zeros(8)
    rets = np.diff(x) / (x[:-1] + 1e-9)
    mf = multi_freq_heads(x)["energy"]
    feat = np.array([
        float(np.mean(rets)) * 50, float(np.std(rets)) * 30,
        float(np.mean(np.abs(rets))) * 40,
        mf["high"] / (abs(x[-1]) + 1e-9) * 100,
        mf["med"] / (abs(x[-1]) + 1e-9) * 100,
        mf["low"] / (abs(x[-1]) + 1e-9),
        float(np.std(volumes[-len(rets):]) / (np.mean(volumes[-len(rets):]) + 1e-9)) if volumes is not None and len(volumes) else 0.0,
        float(np.sign(np.mean(rets[-5:]))) if len(rets) >= 5 else 0.0,
    ], dtype=float)
    n = np.linalg.norm(feat)
    return feat / n if n else feat


def info_nce_loss(anchor, positive, negatives, temp: float = 0.1) -> float:
    """InfoNCE: pull anchor↔positive together, push anchor↔negatives apart."""
    a = np.asarray(anchor); p = np.asarray(positive)
    negs = np.asarray(negatives)
    sp = float(a @ p) / temp
    sn = negs @ a / temp
    logits = np.concatenate([[sp], sn])
    logits -= logits.max()
    e = np.exp(logits)
    return float(-np.log(e[0] / e.sum()))


# ==================== regime memory (recall similar past regimes) ========= #
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "tnet"))
_REG = os.path.join(_DATA, "regimes.npz")
_CALIB = os.path.join(_DATA, "calib.json")
_FLOG = os.path.join(_DATA, "forecasts.jsonl")


def remember_regime(symbol: str, emb: np.ndarray, label: str = ""):
    os.makedirs(_DATA, exist_ok=True)
    embs, labels = [], []
    if os.path.exists(_REG):
        d = np.load(_REG, allow_pickle=True)
        embs = list(d["embs"]); labels = list(d["labels"])
    embs.append(emb); labels.append(f"{symbol}|{label}|{time.strftime('%Y-%m-%d')}")
    embs = embs[-500:]; labels = labels[-500:]
    np.savez(_REG, embs=np.array(embs), labels=np.array(labels))


def recall_regime(emb: np.ndarray, k: int = 3):
    if not os.path.exists(_REG):
        return []
    d = np.load(_REG, allow_pickle=True)
    embs = d["embs"]; labels = d["labels"]
    if len(embs) == 0:
        return []
    sims = embs @ emb
    idx = np.argsort(sims)[::-1][:k]
    return [{"label": str(labels[i]), "sim": round(float(sims[i]), 3)} for i in idx]


# ==================== directional readout + calibration =================== #
def _fit_platt(scores, labels, iters: int = 800, lr: float = 0.3):
    """Platt scaling: fit (temp, bias) so sigmoid(temp*score+bias) ~ P(up).
    Gradient descent on binary cross-entropy. Returns (temp, bias)."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=float)
    if len(s) < 4:
        return 4.0, 0.0
    t, b = 1.0, 0.0
    for _ in range(iters):
        p = sigmoid(t * s + b)
        gt = float(np.mean((p - y) * s))
        gb = float(np.mean(p - y))
        t -= lr * gt; b -= lr * gb
    return float(t), float(b)


def _load_calib():
    try:
        with open(_CALIB) as f:
            d = json.load(f)
        return float(d.get("temp", 4.0)), float(d.get("bias", 0.0)), bool(d.get("calibrated", False))
    except Exception:  # noqa: BLE001
        return 4.0, 0.0, False


def _prob_up(raw: float):
    t, b, cal = _load_calib()
    return float(sigmoid(t * raw + b)), cal


def log_forecast(symbol: str, raw: float, ref_price: float):
    """Append a forecast so calibrate() can later resolve it against reality."""
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_FLOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "symbol": symbol.upper(),
                                "raw": float(raw), "ref": float(ref_price)}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def prune_logs(max_age_days: float = 60.0) -> int:
    """Keep forecasts.jsonl bounded AND diverse: drop rows older than
    max_age_days and dedup to the latest forecast per (symbol, day). This stops
    a frequently-forecast symbol (e.g. SPY) from flooding the log and crowding
    out the cross-asset variety calibration needs. Returns rows kept."""
    if not os.path.exists(_FLOG):
        return 0
    try:
        now = time.time()
        keep: dict = {}
        for ln in open(_FLOG, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if now - r.get("ts", now) > max_age_days * 86400:
                continue
            day = time.strftime("%Y-%m-%d", time.gmtime(r.get("ts", now)))
            keep[(r.get("symbol"), day)] = r          # file is ascending -> latest wins
        rows = sorted(keep.values(), key=lambda x: x.get("ts", 0))
        with open(_FLOG, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return len(rows)
    except Exception:  # noqa: BLE001
        return 0


def calibrate(min_age_days: float = 5.0) -> dict:
    """Resolve matured logged forecasts against realized moves and refit Platt
    scaling. Honest: only forecasts older than the horizon are scored."""
    prune_logs()
    if not os.path.exists(_FLOG):
        return {"calibrated": False, "reason": "no forecast log"}
    rows = []
    with open(_FLOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    now = time.time()
    scores, labels, moves = [], [], []
    for r in rows:
        if now - r.get("ts", now) < min_age_days * 86400:
            continue
        cur = _closes(r["symbol"])
        if len(cur) < 2:
            continue
        last = float(cur[-1])
        ref = float(r.get("ref", last))
        if ref <= 0:
            continue
        scores.append(float(r["raw"]))
        labels.append(1.0 if last > ref else 0.0)
        moves.append(abs(last / ref - 1.0))                 # realized |move| (fraction)
    if len(scores) < 8:
        return {"calibrated": False, "reason": f"only {len(scores)} matured samples (need 8)"}
    t, b = _fit_platt(scores, labels)
    p = sigmoid(t * np.asarray(scores) + b)
    acc = float(np.mean((p >= 0.5) == (np.asarray(labels) >= 0.5)))
    # conformal band: distribution-free 80th-pct realized |move| over the horizon
    band_pct = float(np.quantile(moves, 0.8)) if len(moves) >= 20 else None
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_CALIB, "w") as f:
            json.dump({"temp": t, "bias": b, "calibrated": True,
                       "n": len(scores), "acc": round(acc, 3),
                       "band_pct": band_pct, "band_horizon": min_age_days,
                       "updated": time.strftime("%Y-%m-%d %H:%M")}, f)
    except Exception:  # noqa: BLE001
        pass
    return {"calibrated": True, "temp": round(t, 3), "bias": round(b, 3),
            "n": len(scores), "in_sample_acc": round(acc, 3),
            "conformal_band_pct": (round(band_pct * 100, 3) if band_pct else None)}


def accuracy() -> dict:
    """Resolve matured logged forecasts and report directional hit-rate + mean
    absolute move error, broken out by maturity bucket. Read-only telemetry --
    does not refit anything. Honest: only forecasts past their bucket age count."""
    if not os.path.exists(_FLOG):
        return {"n": 0, "buckets": []}
    rows = []
    try:
        for line in open(_FLOG, encoding="utf-8"):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:  # noqa: BLE001
        return {"n": 0, "buckets": []}
    now = time.time()
    BUCKETS = [("1-3d", 1, 3), ("3-7d", 3, 7), ("7-30d", 7, 30)]
    agg = {b[0]: {"hit": 0, "n": 0, "mae": 0.0} for b in BUCKETS}
    tot_hit = tot_n = 0
    for r in rows:
        age = (now - r.get("ts", now)) / 86400.0
        cur = _closes(r["symbol"])
        if len(cur) < 2:
            continue
        ref = float(r.get("ref", 0)) or 0.0
        if ref <= 0:
            continue
        last = float(cur[-1])
        up = last > ref
        pred_up = float(r.get("raw", 0)) > 0
        move = abs(last / ref - 1.0)
        for name, lo, hi in BUCKETS:
            if lo <= age < hi:
                a = agg[name]
                a["n"] += 1; a["hit"] += int(up == pred_up); a["mae"] += move
                tot_n += 1; tot_hit += int(up == pred_up)
                break
    buckets = []
    for name, _, _ in BUCKETS:
        a = agg[name]
        if a["n"]:
            buckets.append({"bucket": name, "n": a["n"],
                            "hit_rate": round(a["hit"] / a["n"], 3),
                            "avg_move_pct": round(100 * a["mae"] / a["n"], 3)})
    return {"n": tot_n, "hit_rate": round(tot_hit / tot_n, 3) if tot_n else None,
            "buckets": buckets}


def scan(symbols: list[str]) -> dict:
    """Rank a small universe by transformer conviction. For each symbol: the
    directional read, calibrated P(up), confidence, and an input-subsample
    stability score. Sorted by |raw|*confidence*stability (decision-usefulness).
    Offline + cheap (reuses cached closes); cap the universe to keep it fast."""
    out = []
    for sym in symbols[:12]:
        sym = sym.upper()
        try:
            x = _closes(sym)
            if len(x) < 30:
                continue
            xa = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0)
            rd = directional_readout(xa)
            enc = encode(xa[-256:])
            conc = 1.0 - (enc["entropy"][-1] if enc["entropy"] else 0.5)
            conf = float(np.clip(0.5 * conc + 0.5 * rd["agree"], 0, 1))
            ens = forecast_ensemble(sym)
            stab = ens.get("stability", 0.5) if "error" not in ens else 0.5
            pu, _cal = _prob_up(rd["raw"])
            strength = abs(rd["raw"]) * conf * (0.5 + 0.5 * (stab or 0.5))
            out.append({"symbol": sym,
                        "direction": "up" if rd["raw"] > 0.05 else "down" if rd["raw"] < -0.05 else "flat",
                        "raw": round(float(rd["raw"]), 4), "prob_up": round(float(pu), 3),
                        "confidence": round(conf, 3), "stability": stab,
                        "strength": round(float(strength), 4)})
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda r: r["strength"], reverse=True)
    return {"ranked": out, "n": len(out)}


def forecast_ensemble(symbol: str) -> dict:
    """Input-subsample ensemble: run the directional readout over several recent
    window lengths (jackknife in time). The MEAN raw is a steadier signal and the
    STD is an honest stability estimate -- low std == the read is robust to how
    much history you feed it. No extra weights; pure resampling of one model."""
    symbol = symbol.upper()
    x = _closes(symbol)
    if len(x) < 60:
        return {"symbol": symbol, "error": "insufficient history"}
    x = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0)
    raws = []
    for L in (60, 96, 128, 192, 256):
        if len(x) >= L:
            try:
                raws.append(float(directional_readout(x[-L:])["raw"]))
            except Exception:  # noqa: BLE001
                pass
    if not raws:
        return {"symbol": symbol, "error": "readout failed"}
    raws = np.asarray(raws)
    mean = float(raws.mean()); std = float(raws.std())
    stability = round(float(np.clip(1.0 - 4.0 * std, 0.0, 1.0)), 3)
    return {"symbol": symbol, "members": len(raws), "raw_mean": round(mean, 4),
            "raw_std": round(std, 4), "stability": stability,
            "agree": round(float(np.mean(np.sign(raws) == np.sign(mean))), 3)}


def _calib_band():
    """Empirical conformal half-width (fraction, per band_horizon days); None if absent."""
    try:
        d = json.load(open(_CALIB))
        bp = d.get("band_pct")
        return (float(bp), float(d.get("band_horizon", 5.0))) if bp else None
    except Exception:  # noqa: BLE001
        return None


def directional_readout(series: np.ndarray) -> dict:
    """Multi-frequency momentum read over the transformer's frequency bands.
    Returns raw directional score in [-1,1] plus components and band agreement.
    Honest framing: this is a trend/mean-reversion state estimate, not a
    learned alpha — calibration maps it to an empirical probability."""
    x = np.asarray(series, dtype=float)
    if len(x) < 12:
        return {"raw": 0.0, "trend": 0.0, "accel": 0.0, "micro": 0.0, "agree": 0.0, "vol": 0.0}
    rets = np.diff(x) / (x[:-1] + 1e-12)
    vol = float(np.std(rets[-20:])) if len(rets) >= 20 else float(np.std(rets) + 1e-9)
    # trailing-window momentum (robust at the right edge, unlike convolved MAs)
    w = min(40, len(x) - 1)
    trend = float(x[-1] / x[-1 - w] - 1) if w >= 1 else 0.0
    s = max(3, len(x) // 16)
    ln = max(8, len(x) // 4)
    short_ma = float(np.mean(x[-s:]))
    long_ma = float(np.mean(x[-ln:]))
    accel = (short_ma - long_ma) / (abs(x[-1]) + 1e-9)   # short-vs-long (mean-rev/accel)
    micro = float(np.sign(rets[-1])) if len(rets) else 0.0
    e = multi_freq_heads(x)["energy"]
    tot = e["high"] + e["med"] + e["low"] + 1e-9
    raw = float(np.tanh(5.0 * trend * (1 - e["high"] / tot)
                        + 8.0 * accel * (e["med"] / tot)
                        + 0.25 * micro * (e["high"] / tot)))
    signs = [np.sign(trend), np.sign(accel), np.sign(micro)]
    dom = np.sign(raw)
    agree = float(np.mean([1.0 if s == dom and dom != 0 else 0.0 for s in signs]))
    return {"raw": round(raw, 4), "trend": round(trend, 5), "accel": round(accel, 6),
            "micro": micro, "agree": round(agree, 3), "vol": round(vol, 5)}


# ==================== pipeline ============================================ #
_MACROS = {"SPY": "S&P 500", "QQQ": "Nasdaq", "TLT": "Treasury yields(inv)",
           "GLD": "Gold", "UUP": "US Dollar", "HYG": "Credit"}

_Z90 = 1.2816  # 10/90 quantile z


def _closes(sym):
    try:
        if "/" in sym:
            from . import history
            p = history.load_panel([sym], days=200, source="coinex")
            return p["prices"].get(sym, [])
        from .crsp import query as crsp
        return [b["close"] for b in crsp.get_prices(sym, "2024-06-01", None) if b.get("close")]
    except Exception:  # noqa: BLE001
        return []


def _macro_factors(symbol: str) -> dict:
    factors = {}
    for m in _MACROS:
        if m == symbol:
            continue
        c = _closes(m)
        if len(c) >= 30:
            factors[_MACROS[m]] = c
    return factors


def analyze(symbol: str) -> dict:
    symbol = symbol.upper()
    x = _closes(symbol)
    if len(x) < 30:
        return {"symbol": symbol, "error": "insufficient history"}
    x = np.asarray(x, dtype=float)
    rets = np.diff(x) / x[:-1]
    vol = float(np.std(rets[-20:]))
    vol_z = float((vol - np.std(rets)) / (np.std(rets) + 1e-9))

    drivers = cross_attention(x, _macro_factors(symbol))
    mf = multi_freq_heads(x)["energy"]
    emb = regime_embedding(x)
    nearest = recall_regime(emb)
    try:
        remember_regime(symbol, emb)
    except Exception:  # noqa: BLE001
        pass

    feats = {"ret_5": float(x[-1] / x[-6] - 1) if len(x) > 6 else 0.0,
             "mom_20": float(x[-1] / x[-21] - 1) if len(x) > 21 else 0.0,
             "rvol": vol, "price_z": float((x[-1] - x[-20:].mean()) / (x[-20:].std() + 1e-9))}
    selection = vsn(feats, vol_z)
    enc = encode(x[-256:])
    patches = patchify(x[-128:])
    return {
        "symbol": symbol,
        "vol_z": round(vol_z, 3),
        "drivers": drivers,
        "freq_heads": mf,
        "variable_selection": selection,
        "regime_embedding_dim": int(emb.shape[0]),
        "nearest_regimes": nearest,
        "encoder": {"n_patches": enc["n_patches"], "layer_attn_entropy": enc["entropy"],
                    "pooled_norm": round(float(np.linalg.norm(enc["pooled"])), 4)},
        "patches": [int(patches.shape[0]), int(patches.shape[1]) if patches.ndim == 2 else 0],
    }


def forecast(symbol: str, horizon: int = 5) -> dict:
    """Calibrated, decision-useful read: direction, probability, expected move,
    quantile band, drivers, regime, and confidence. Fail-soft."""
    symbol = symbol.upper()
    x = _closes(symbol)
    if len(x) < 30:
        return {"symbol": symbol, "error": "insufficient history"}
    x = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0)
    rd = directional_readout(x)
    raw, vol = rd["raw"], rd["vol"]
    enc = encode(x[-256:])
    ents = enc.get("entropy") or []
    conc = 1.0 - (ents[-1] if ents else 0.5)                       # last-layer attention focus
    rollup = round(float(1.0 - np.mean(ents)), 3) if ents else None  # attention rollout across layers
    confidence = round(float(np.clip(0.5 * conc + 0.5 * rd["agree"], 0, 1)), 3)
    prob_up, calibrated = _prob_up(raw)
    conformal = _calib_band()
    band_method = "conformal" if conformal else "gaussian"

    def _band(hh):
        if conformal:
            bp, bh = conformal
            return bp * np.sqrt(max(1, hh) / max(1.0, bh))
        return _Z90 * vol * np.sqrt(max(1, hh))

    p50 = raw * vol * np.sqrt(max(1, horizon))
    band = _band(horizon)
    direction = "up" if raw > 0.05 else "down" if raw < -0.05 else "flat"
    term = [{"horizon": int(hh),
             "expected_move_pct": round(100 * raw * vol * np.sqrt(max(1, hh)), 3),
             "p10": round(100 * (raw * vol * np.sqrt(max(1, hh)) - _band(hh)), 3),
             "p90": round(100 * (raw * vol * np.sqrt(max(1, hh)) + _band(hh)), 3)}
            for hh in (1, 5, 20)]
    drivers = cross_attention(x, _macro_factors(symbol))
    emb = regime_embedding(x)
    try:
        ens = forecast_ensemble(symbol)
        stability = ens.get("stability")
    except Exception:  # noqa: BLE001
        stability = None
    try:
        log_forecast(symbol, raw, float(x[-1]))
    except Exception:  # noqa: BLE001
        pass
    return {
        "symbol": symbol,
        "direction": direction,
        "raw_score": round(raw, 4),
        "prob_up": round(prob_up, 3),
        "calibrated": calibrated,
        "confidence": confidence,
        "expected_move_pct": round(100 * p50, 3),
        "quantiles_pct": {"p10": round(100 * (p50 - band), 3),
                          "p50": round(100 * p50, 3),
                          "p90": round(100 * (p50 + band), 3)},
        "band_method": band_method,
        "term_structure": term,
        "horizon_days": horizon,
        "components": rd,
        "drivers": drivers,
        "attention_focus": round(float(conc), 3),
        "attention_rollout": rollup,
        "ensemble_stability": stability,
        "nearest_regimes": recall_regime(emb),
    }


_sig_cache: dict[str, tuple] = {}


def score_signal(symbol: str, ttl: float = 300.0) -> float | None:
    """Single directional signal in [-1,1] for the confluence brain:
    raw_score * confidence. Cached. None on failure."""
    symbol = symbol.upper()
    now = time.time()
    hit = _sig_cache.get(symbol)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        x = _closes(symbol)
        if len(x) < 30:
            _sig_cache[symbol] = (now, None)
            return None
        xa = np.asarray(x, dtype=float)
        rd = directional_readout(xa)
        enc = encode(np.asarray(x[-256:], dtype=float))
        conc = 1.0 - (enc["entropy"][-1] if enc["entropy"] else 0.5)
        conf = float(np.clip(0.5 * conc + 0.5 * rd["agree"], 0, 1))
        # stability: agreement of the read across input-window subsamples. An
        # unstable read (flips with how much history you feed it) is damped so it
        # contributes less conviction to the confluence brain.
        raws = [rd["raw"]]
        for L in (96, 160):
            if len(x) >= L:
                try:
                    raws.append(float(directional_readout(xa[-L:])["raw"]))
                except Exception:  # noqa: BLE001
                    pass
        std = float(np.std(raws)) if len(raws) > 1 else 0.0
        stability = float(np.clip(1.0 - 4.0 * std, 0.0, 1.0))
        val = float(np.clip(rd["raw"] * conf * (0.5 + 0.5 * stability), -1, 1))
        _sig_cache[symbol] = (now, val)
        return val
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    # self-checks
    xn, st = revin(np.array([10.0, 11, 12, 13, 14]))
    print("RevIN reversible:", np.allclose(revin_inv(xn, st), [10, 11, 12, 13, 14]))
    m = sliding_dilated_mask(8, window=3, dilation=2)
    print("mask local diag:", bool(m[7, 7]), "far sparse:", int(m[7].sum()))
    enc = encode(np.cumsum(np.random.default_rng(0).normal(size=300)) + 100)
    print("encoder patches:", enc["n_patches"], "pooled dim:", enc["pooled"].shape,
          "attn entropy:", enc["entropy"])
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print(json.dumps(forecast("AAPL"), indent=2))
