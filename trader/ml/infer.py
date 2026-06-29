"""Live inference: load the champion model once and score price histories.
Returns ml_score in [-1,1] (=2*P(up)-1), or None if no model / too little data.
"""
from __future__ import annotations

import time

from .features import feature_vector
from .model import LogisticModel, MODEL_PATH

_cache = {"mtime": None, "model": None}


def _model():
    import os
    if not os.path.exists(MODEL_PATH):
        return None
    mt = os.path.getmtime(MODEL_PATH)
    if _cache["mtime"] != mt:                  # hot-reload after a retrain
        _cache["model"] = LogisticModel.load(MODEL_PATH)
        _cache["mtime"] = mt
    return _cache["model"]


def score_from_closes(closes) -> float | None:
    m = _model()
    if m is None:
        return None
    vec, _ = feature_vector(closes)
    if vec is None:
        return None
    try:
        return round(m.score_one(vec), 3)
    except Exception:  # noqa: BLE001
        return None


def model_card() -> dict:
    m = _model()
    if m is None:
        return {"trained": False}
    return {"trained": True, **(m.meta or {}), "importances": m.importances()}


_px_cache: dict[str, tuple] = {}


def score_symbol(symbol: str, ttl: float = 300) -> float | None:
    """Score a symbol using CRSP-lite cached prices (offline, cached)."""
    now = time.time()
    if symbol in _px_cache and now - _px_cache[symbol][0] < ttl:
        return _px_cache[symbol][1]
    try:
        from ..crsp import query as crsp
        bars = crsp.get_prices(symbol, "2024-01-01", None)
        closes = [b["close"] for b in bars if b.get("close")]
        s = score_from_closes(closes)
    except Exception:  # noqa: BLE001
        s = None
    _px_cache[symbol] = (now, s)
    return s


if __name__ == "__main__":
    import json
    print(json.dumps(model_card(), indent=2)[:600])
    for t in ("AAPL", "XOM", "JNJ"):
        print(t, score_symbol(t))
