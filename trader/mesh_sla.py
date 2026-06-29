"""Mesh per-layer SLA monitor -- is each layer still publishing on cadence?

A thin analytics layer over the shared insight mesh. Each layer (brain,
prediction, ml, tnet, news, cortex, reasoning, ...) is expected to keep
publishing insights at roughly its historical rate. If a layer goes quiet --
its most recent insight is far older than its typical inter-arrival gap -- that
usually means the daemon feeding it died. This module flags those layers.

Public API:
    sla(window=400) -> {"layers": [...], "generated": iso}
        each layer: {layer, count, last_seen_min, median_gap_min,
                     expected_min, status}
    overdue()       -> [{layer, status, last_seen_min}, ...]  (status != "ok")

Per layer (over mesh.recent(window)):
    count           = number of insights from that layer in the window
    last_seen_min   = minutes since its most recent insight
    median_gap_min  = median inter-arrival gap (minutes) between consecutive
                      insights; None if fewer than 3 samples
    expected_min    = 2.5 * median_gap_min (tolerance band); None if no median
    status:
        "stale" if last_seen_min > 3*median_gap_min (median known), OR
                   last_seen_min > 180 with fewer than 2 samples
        "slow"  if last_seen_min > expected_min
        "ok"    otherwise
Layers are sorted by status severity (stale, slow, ok) then last_seen_min desc.

Everything is fail-soft: any error yields an empty/zero result rather than
raising.
"""
from __future__ import annotations

import calendar
import time

from . import mesh

_SEVERITY = {"stale": 0, "slow": 1, "ok": 2}


def _now() -> float:
    return time.time()


def _epoch(iso: str) -> float:
    """Parse a mesh ISO timestamp to epoch seconds; default to now on failure."""
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return _now()


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _layers(window: int):
    """Internal: return list of per-layer SLA dicts sorted by severity then
    last_seen_min desc."""
    try:
        rows = mesh.recent(n=window)
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return []

    now = _now()

    # group epoch timestamps per layer
    by_layer: dict[str, list] = {}
    for r in rows:
        try:
            lay = r.get("layer") or ""
            ts = r.get("ts") or ""
        except Exception:  # noqa: BLE001
            continue
        by_layer.setdefault(lay, []).append(_epoch(ts))

    out = []
    for lay, epochs in by_layer.items():
        try:
            epochs = sorted(epochs)  # oldest -> newest
            count = len(epochs)
            last = epochs[-1]
            last_seen_min = max(0.0, (now - last) / 60.0)

            # inter-arrival gaps between consecutive insights (minutes)
            gaps = [(epochs[i] - epochs[i - 1]) / 60.0 for i in range(1, count)]
            median_gap_min = _median(gaps) if len(gaps) >= 2 else None
            # len(gaps) >= 2 means >= 3 samples
            expected_min = (2.5 * median_gap_min) if median_gap_min else None

            status = "ok"
            if median_gap_min:
                if last_seen_min > 3.0 * median_gap_min:
                    status = "stale"
                elif expected_min is not None and last_seen_min > expected_min:
                    status = "slow"
            else:
                # too few samples to know the cadence: only flag long silences
                if count < 2 and last_seen_min > 180.0:
                    status = "stale"

            out.append({
                "layer": lay,
                "count": int(count),
                "last_seen_min": round(last_seen_min, 2),
                "median_gap_min": (round(median_gap_min, 2)
                                   if median_gap_min is not None else None),
                "expected_min": (round(expected_min, 2)
                                 if expected_min is not None else None),
                "status": status,
            })
        except Exception:  # noqa: BLE001
            continue

    out.sort(key=lambda x: (_SEVERITY.get(x["status"], 3), -x["last_seen_min"]))
    return out


def sla(window: int = 400) -> dict:
    """Per-layer cadence SLA report.

    Returns ``{"layers": [...], "generated": iso}``.
    """
    try:
        win = max(1, int(window))
    except Exception:  # noqa: BLE001
        win = 400
    try:
        layers = _layers(win)
    except Exception:  # noqa: BLE001
        layers = []
    return {"layers": layers, "generated": mesh._now()}


def overdue() -> list:
    """Layers whose status is not "ok", each as
    ``{layer, status, last_seen_min}``."""
    try:
        layers = _layers(400)
    except Exception:  # noqa: BLE001
        layers = []
    return [
        {"layer": l["layer"], "status": l["status"],
         "last_seen_min": l["last_seen_min"]}
        for l in layers if l.get("status") != "ok"
    ]
