"""Mesh layer forecasting -- predict which layer is likely to fire next.

A first-order Markov transition model over the layer firing stream. We read the
recent insight mesh (which arrives NEWEST FIRST), reverse it into chronological
order, then count how often layer[i] is immediately followed by layer[i+1].
Normalizing each row turns those counts into transition probabilities, giving a
cheap, explainable read on "given the last layer that fired, what fires next".

Everything is fail-soft: a broken mesh, missing keys, or odd rows never raise --
they just yield a smaller (or empty) transition model.

Public API:
  transitions(window=400) -> {"matrix": {layer: {next_layer: prob}}, "counts": int, "generated": iso}
  predict_next(layer, window=400, top=3) -> [{"layer", "prob"}]
  most_likely(window=400) -> {"from", "to", "prob"} | {}
"""
from __future__ import annotations

import time

try:  # numpy is allowed but never required
    from . import mesh
except Exception:  # noqa: BLE001  -- import must never hard-fail callers
    mesh = None  # type: ignore


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _layer_sequence(window: int) -> list:
    """Pull recent insights and return the layer stream in CHRONOLOGICAL order.

    mesh.recent() returns newest-first, so we reverse it. Rows without a usable
    layer are dropped. Fail-soft: any error yields an empty sequence.
    """
    rows = []
    if mesh is not None:
        try:
            rows = mesh.recent(max(1, int(window))) or []
        except Exception:  # noqa: BLE001
            rows = []

    seq = []
    for r in rows:
        try:
            layer = (r.get("layer", "") if isinstance(r, dict) else "") or ""
        except Exception:  # noqa: BLE001
            continue
        layer = str(layer).strip()
        if layer:
            seq.append(layer)

    seq.reverse()  # newest-first -> chronological (oldest-first)
    return seq


def transitions(window: int = 400) -> dict:
    """Build the first-order Markov transition matrix over the layer stream.

    Counts layer[i] -> layer[i+1] across the chronological sequence, then
    normalizes each source row into probabilities. ``counts`` is the total
    number of transitions observed.

    Returns {"matrix": {layer: {next_layer: prob}}, "counts": int, "generated": iso}.
    """
    seq = _layer_sequence(window)

    raw: dict[str, dict[str, int]] = {}
    total = 0
    for a, b in zip(seq, seq[1:]):
        row = raw.setdefault(a, {})
        row[b] = row.get(b, 0) + 1
        total += 1

    matrix: dict[str, dict[str, float]] = {}
    for src, nexts in raw.items():
        row_total = sum(nexts.values())
        if row_total <= 0:
            continue
        matrix[src] = {
            dst: round(cnt / row_total, 6) for dst, cnt in nexts.items()
        }

    return {"matrix": matrix, "counts": total, "generated": _now()}


def predict_next(layer: str, window: int = 400, top: int = 3) -> list:
    """Top-N most likely next layers after ``layer`` (empty if unseen)."""
    try:
        src = str(layer or "").strip()
        if not src:
            return []
        matrix = transitions(window).get("matrix", {})
        row = matrix.get(src) or {}
        if not row:
            return []
        ranked = sorted(row.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        n = max(0, int(top))
        return [{"layer": dst, "prob": prob} for dst, prob in ranked[:n]]
    except Exception:  # noqa: BLE001
        return []


def most_likely(window: int = 400) -> dict:
    """Single highest-probability transition across the whole matrix.

    Returns {"from", "to", "prob"} or {} when there is nothing to report.
    """
    try:
        matrix = transitions(window).get("matrix", {})
        best = {}
        best_prob = -1.0
        for src in sorted(matrix.keys()):
            for dst, prob in sorted(matrix[src].items()):
                if prob > best_prob:
                    best_prob = prob
                    best = {"from": src, "to": dst, "prob": prob}
        return best
    except Exception:  # noqa: BLE001
        return {}
