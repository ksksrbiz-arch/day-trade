"""Mesh theme clustering -- distill recurring topics from recent insight text.

Lightweight, dependency-free theme extraction over the insight mesh: pull recent
insights, tokenize their free text, and cluster by salience-weighted token
frequency. Each surviving token becomes a candidate "theme" carrying how often it
appeared (count), the accumulated salience behind it (weight), and the set of
layers that surfaced it. This gives the dashboard/awareness layer a cheap,
explainable read on "what is the mesh talking about right now" without any model.

Everything is fail-soft: a broken mesh, missing keys, or odd text never raises --
it just yields fewer (or zero) themes.

Public API:
  themes(window=200, top=8) -> {"themes": [{term,count,weight,layers}], "generated": iso}
  top_terms(n=5)            -> list[str]
"""
from __future__ import annotations

import re
import time

try:  # numpy is allowed but never required
    from . import mesh
except Exception:  # noqa: BLE001  -- import must never hard-fail callers
    mesh = None  # type: ignore

# ~40 common, low-signal words to drop so themes surface domain terms, not glue.
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "has",
    "had", "new", "top", "via", "per", "out", "into", "over", "under", "more",
    "less", "than", "then", "now", "its", "our", "all", "any", "not", "but",
    "you", "your", "what", "when", "will", "can", "get", "got", "set", "one",
    "two", "etc", "are", "have", "been", "they", "them",
    # system/noise tokens common in mesh insight text
    "news", "stale", "missing", "model", "regime", "posture", "signal", "signals",
    "layer", "layers", "decision", "decisions", "calibration", "calibrated",
    "update", "updated", "wen", "30d", "20d", "5d", "1d", "acc", "conf", "net",
    "digest", "forecast", "weights", "watching", "resolved", "matrix", "snapshot",
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokenize(text: str):
    """Lowercase, split on non-alphanumerics, drop short tokens, pure numbers,
    and stopwords. Yields the surviving tokens."""
    if not text:
        return
    for tok in _TOKEN_SPLIT.split(str(text).lower()):
        if len(tok) < 3:
            continue
        if tok.isdigit():
            continue
        if tok in STOPWORDS:
            continue
        yield tok


def themes(window: int = 200, top: int = 8) -> dict:
    """Cluster recent insight text into themes by salience-weighted token freq.

    Returns {"themes": [{term, count, weight, layers}], "generated": iso}.
    Ranked by accumulated salience weight (count as a tie-break), top N kept.
    """
    rows = []
    if mesh is not None:
        try:
            rows = mesh.recent(window) or []
        except Exception:  # noqa: BLE001
            rows = []

    counts: dict[str, int] = {}
    weights: dict[str, float] = {}
    layers: dict[str, set] = {}

    for r in rows:
        try:
            text = r.get("text", "") if isinstance(r, dict) else ""
            sal = float(r.get("salience", 0.0) or 0.0) if isinstance(r, dict) else 0.0
            layer = (r.get("layer", "") if isinstance(r, dict) else "") or ""
        except Exception:  # noqa: BLE001
            continue
        seen_here = set()
        for tok in _tokenize(text):
            counts[tok] = counts.get(tok, 0) + 1
            weights[tok] = weights.get(tok, 0.0) + sal
            if layer:
                layers.setdefault(tok, set()).add(layer)
            seen_here.add(tok)

    ranked = sorted(
        counts.keys(),
        key=lambda t: (weights.get(t, 0.0), counts.get(t, 0)),
        reverse=True,
    )

    out = []
    for term in ranked[: max(0, int(top))]:
        out.append({
            "term": term,
            "count": counts.get(term, 0),
            "weight": round(weights.get(term, 0.0), 3),
            "layers": sorted(layers.get(term, set())),
        })

    return {"themes": out, "generated": _now()}


def top_terms(n: int = 5) -> list:
    """Just the ranked theme term strings (convenience accessor)."""
    try:
        data = themes(top=max(1, int(n)))
        return [t["term"] for t in data.get("themes", [])][: max(0, int(n))]
    except Exception:  # noqa: BLE001
        return []
