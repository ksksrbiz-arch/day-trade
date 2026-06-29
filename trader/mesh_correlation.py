"""Mesh lead/lag correlation -- which layer tends to fire BEFORE another.

Lead/lag analysis over the insight mesh: when layer A publishes about a symbol,
does layer B tend to follow shortly after? Aggregating these directed
observations across symbols surfaces predictive "lead" layers -- the ones whose
chatter reliably precedes another layer's.

Public API:
  lead_lag(window=500, max_gap_min=30.0) -> {"pairs": [...], "generated": iso}
  top_leads(n=5)                          -> first n pairs

Everything is fail-soft: a malformed timestamp or a misbehaving mesh just yields
fewer (or no) observations rather than raising.
"""
from __future__ import annotations

import calendar
import time

from . import mesh


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(ts: str):
    """Parse an ISO UTC mesh timestamp to epoch seconds, or None on failure."""
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return None


def lead_lag(window: int = 500, max_gap_min: float = 30.0) -> dict:
    """Directed lead->follow observations across the recent mesh.

    Pull the most recent ``window`` insights, group by symbol (skipping empty
    symbols), and within each symbol walk its insights in time order. For every
    ordered pair where a later insight from a *different* layer follows an
    earlier one within ``max_gap_min`` minutes, record a directed
    (lead -> follow) observation with the gap in minutes. Aggregate across all
    symbols into per-(lead, follow) counts and average gaps, keeping only pairs
    seen at least twice.

    Returns ``{"pairs": [{"lead","follow","count","avg_gap_min"}, ...],
    "generated": iso}`` sorted by count desc, then avg_gap_min asc.
    """
    try:
        rows = mesh.recent(window) or []
    except Exception:  # noqa: BLE001
        rows = []

    try:
        max_gap_sec = float(max_gap_min) * 60.0
    except Exception:  # noqa: BLE001
        max_gap_sec = 30.0 * 60.0

    # Group insights by symbol, carrying (epoch, layer).
    by_symbol: dict[str, list] = {}
    for r in rows:
        try:
            sym = (r.get("symbol") or "").strip()
            layer = (r.get("layer") or "").strip()
            ts = r.get("ts") or ""
        except Exception:  # noqa: BLE001
            continue
        if not sym or not layer:
            continue
        ep = _epoch(ts)
        if ep is None:
            continue
        by_symbol.setdefault(sym, []).append((ep, layer))

    # Aggregate directed observations across all symbols.
    agg: dict[tuple, dict] = {}  # (lead, follow) -> {"count", "gap_sum"}
    for events in by_symbol.values():
        events.sort(key=lambda e: e[0])  # ascending by epoch
        for i in range(len(events)):
            lead_ep, lead_layer = events[i]
            for j in range(i + 1, len(events)):
                follow_ep, follow_layer = events[j]
                gap = follow_ep - lead_ep
                if gap <= 0:
                    continue
                if gap > max_gap_sec:
                    break  # events sorted; everything later is further out
                if follow_layer == lead_layer:
                    continue
                key = (lead_layer, follow_layer)
                slot = agg.setdefault(key, {"count": 0, "gap_sum": 0.0})
                slot["count"] += 1
                slot["gap_sum"] += gap

    pairs = []
    for (lead, follow), slot in agg.items():
        cnt = slot["count"]
        if cnt < 2:
            continue
        avg_gap_min = round((slot["gap_sum"] / cnt) / 60.0, 2)
        pairs.append({"lead": lead, "follow": follow,
                      "count": cnt, "avg_gap_min": avg_gap_min})

    pairs.sort(key=lambda p: (-p["count"], p["avg_gap_min"]))
    return {"pairs": pairs, "generated": _now_iso()}


def top_leads(n: int = 5) -> list:
    """The first ``n`` lead->follow pairs from :func:`lead_lag`."""
    try:
        k = int(n)
    except Exception:  # noqa: BLE001
        k = 5
    if k < 0:
        k = 0
    try:
        return lead_lag().get("pairs", [])[:k]
    except Exception:  # noqa: BLE001
        return []


if __name__ == "__main__":
    import json
    print(json.dumps(lead_lag(), indent=2))
