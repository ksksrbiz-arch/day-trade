"""Mesh priority inbox -- rank insights by how much they deserve human attention.

A thin analytics layer over the shared insight mesh. It does NOT touch
``mesh.graph``; per-layer influence is computed inline from the same window so
this module is cheap and self-contained.

Public API:
    inbox(limit=20, window=300) -> {"items": [...], "generated": iso}
    counts(window=300)          -> {"total": int, "high": int}

priority = salience * recency * influence * novelty
    recency   = 1/(1 + age_hours/6)
    influence = per-layer weight; for each layer sum(salience), normalize by the
                busiest layer's sum, then influence = 0.4 + 0.6*normalized.
    novelty   = 1.0 normally, 0.3 if this insight's text (first 60 chars,
                lowercased) duplicates a MORE-RECENT kept item.

Everything is fail-soft: any error yields an empty/zero result rather than
raising.
"""
from __future__ import annotations

import time

from . import mesh


def _now() -> float:
    return time.time()


def _epoch(iso: str) -> float:
    """Parse a mesh ISO timestamp to epoch seconds; default to now on failure."""
    try:
        return time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return _now()


def _key(text: str) -> str:
    return (text or "")[:60].lower()


def _ranked(window: int):
    """Internal: return list of scored item dicts sorted by priority desc."""
    try:
        rows = mesh.recent(n=window)
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return []

    now = _now()

    # --- per-layer influence (inline; do NOT import mesh.graph) ---
    layer_sum: dict[str, float] = {}
    for r in rows:
        try:
            lay = r.get("layer") or ""
            sal = float(r.get("salience") or 0.0)
        except Exception:  # noqa: BLE001
            lay, sal = "", 0.0
        layer_sum[lay] = layer_sum.get(lay, 0.0) + sal
    max_sum = max(layer_sum.values(), default=0.0) or 1.0
    influence = {lay: 0.4 + 0.6 * (s / max_sum) for lay, s in layer_sum.items()}

    # rows come back newest-first from mesh.recent (ORDER BY ts DESC). Walk them
    # in that order so that when we hit a duplicate, the more-recent copy has
    # already been kept and the older one gets novelty-penalized.
    seen_keys: set[str] = set()
    items = []
    for r in rows:
        try:
            text = r.get("text") or ""
            lay = r.get("layer") or ""
            sal = float(r.get("salience") or 0.0)
            ts = r.get("ts") or ""
            age_h = max(0.0, (now - _epoch(ts)) / 3600.0)
            recency = 1.0 / (1.0 + age_h / 6.0)
            infl = influence.get(lay, 0.4)
            k = _key(text)
            novelty = 0.3 if k in seen_keys else 1.0
            seen_keys.add(k)
            priority = round(sal * recency * infl * novelty, 4)
            items.append({
                "id": r.get("id"),
                "layer": lay,
                "symbol": r.get("symbol") or "",
                "text": text,
                "ts": ts,
                "salience": round(sal, 4),
                "priority": priority,
            })
        except Exception:  # noqa: BLE001
            continue

    items.sort(key=lambda x: x["priority"], reverse=True)
    return items


def inbox(limit: int = 20, window: int = 300) -> dict:
    """Top-priority insights for human review.

    Returns ``{"items": [...], "generated": iso}`` where each item has keys
    id, layer, symbol, text, ts, salience, priority.
    """
    try:
        lim = max(0, int(limit))
    except Exception:  # noqa: BLE001
        lim = 20
    try:
        win = max(1, int(window))
    except Exception:  # noqa: BLE001
        win = 300
    try:
        items = _ranked(win)[:lim]
    except Exception:  # noqa: BLE001
        items = []
    return {"items": items, "generated": mesh._now()}


def counts(window: int = 300) -> dict:
    """Totals for the inbox: ``{"total": int, "high": int}`` where high counts
    items with priority >= 0.5."""
    try:
        win = max(1, int(window))
    except Exception:  # noqa: BLE001
        win = 300
    try:
        items = _ranked(win)
    except Exception:  # noqa: BLE001
        items = []
    total = len(items)
    high = sum(1 for it in items if it.get("priority", 0.0) >= 0.5)
    return {"total": int(total), "high": int(high)}
