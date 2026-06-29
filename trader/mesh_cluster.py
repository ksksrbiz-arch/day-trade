"""Mesh symbol clustering -- group co-moving SYMBOLS that surface together.

Two symbols "co-occur" when they show up in insights published by the SAME
layer within the recent mesh window. The more layers two symbols share, the
stronger their co-occurrence weight. Building a symbol-symbol co-occurrence
graph and running a simple connected-components / greedy agglomeration over it
surfaces related names as a group -- e.g. a cluster of tech tickers that the
same layers keep mentioning together.

Public API:
  clusters(window=400, min_size=2) -> {"clusters": [...], "generated": iso}
  related(symbol, window=400, top=5) -> [{"symbol","weight"}, ...]

Everything is fail-soft: a malformed row or a misbehaving mesh just yields
fewer (or no) clusters rather than raising.
"""
from __future__ import annotations

import time

from . import mesh


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cooccur(window: int = 400) -> dict:
    """Build the symbol-symbol co-occurrence weight map.

    Pull the recent ``window`` insights, group symbols by layer, and for every
    unordered pair of distinct symbols that appear in the SAME layer, add to
    their co-occurrence weight (one increment per shared layer).

    Returns ``{symbolA: {symbolB: weight, ...}, ...}`` -- a symmetric mapping.
    """
    try:
        rows = mesh.recent(window) or []
    except Exception:  # noqa: BLE001
        rows = []

    # layer -> set of symbols mentioned by that layer in the window
    by_layer: dict[str, set] = {}
    for r in rows:
        try:
            sym = (r.get("symbol") or "").strip().upper()
            layer = (r.get("layer") or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not sym or not layer:
            continue
        by_layer.setdefault(layer, set()).add(sym)

    weights: dict[str, dict] = {}
    for symbols in by_layer.values():
        syms = sorted(symbols)
        for i in range(len(syms)):
            a = syms[i]
            for j in range(i + 1, len(syms)):
                b = syms[j]
                wa = weights.setdefault(a, {})
                wa[b] = wa.get(b, 0) + 1
                wb = weights.setdefault(b, {})
                wb[a] = wb.get(a, 0) + 1
    return weights


def clusters(window: int = 400, min_size: int = 2) -> dict:
    """Group co-moving symbols into clusters via the co-occurrence graph.

    Symbols are linked when their co-occurrence weight is >= 1 (i.e. they were
    mentioned by at least one common layer). Connected components of that graph
    form the clusters. ``strength`` is the average pairwise co-occurrence weight
    among the symbols in the cluster. Clusters smaller than ``min_size`` are
    dropped. Results are sorted by ``strength`` desc, then size desc.

    Returns ``{"clusters": [{"symbols","size","strength"}, ...],
    "generated": iso}``.
    """
    try:
        min_size = int(min_size)
    except Exception:  # noqa: BLE001
        min_size = 2
    if min_size < 1:
        min_size = 1

    weights = _cooccur(window)

    # Connected components via DFS over the link graph (link iff weight >= 1).
    seen: set = set()
    components: list = []
    for start in weights:
        if start in seen:
            continue
        stack = [start]
        comp: set = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            comp.add(node)
            for nbr, w in weights.get(node, {}).items():
                if w >= 1 and nbr not in seen:
                    stack.append(nbr)
        components.append(comp)

    out = []
    for comp in components:
        if len(comp) < min_size:
            continue
        syms = sorted(comp)
        # Average pairwise co-occurrence within the cluster (missing pair = 0).
        total = 0.0
        npairs = 0
        for i in range(len(syms)):
            for j in range(i + 1, len(syms)):
                total += weights.get(syms[i], {}).get(syms[j], 0)
                npairs += 1
        strength = round(total / npairs, 4) if npairs else 0.0
        out.append({"symbols": syms, "size": len(syms), "strength": strength})

    out.sort(key=lambda c: (-c["strength"], -c["size"]))
    return {"clusters": out, "generated": _now_iso()}


def related(symbol: str, window: int = 400, top: int = 5) -> list:
    """The symbols most co-mentioned with ``symbol`` in the recent window.

    Returns ``[{"symbol","weight"}, ...]`` sorted by weight desc (ties broken
    alphabetically), limited to ``top`` entries. Unknown symbols yield ``[]``.
    """
    try:
        sym = (symbol or "").strip().upper()
    except Exception:  # noqa: BLE001
        sym = ""
    if not sym:
        return []
    try:
        k = int(top)
    except Exception:  # noqa: BLE001
        k = 5
    if k < 0:
        k = 0

    weights = _cooccur(window)
    neighbors = weights.get(sym, {})
    ranked = sorted(neighbors.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"symbol": s, "weight": w} for s, w in ranked[:k]]


if __name__ == "__main__":
    import json
    print(json.dumps(clusters(), indent=2))
