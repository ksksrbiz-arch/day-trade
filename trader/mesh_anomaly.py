"""Anomaly detection over recent shared-mesh activity.

Consumes ``trader.mesh.recent`` (newest-first list of insight dicts) and
surfaces simple, explainable anomalies. Every detector is fail-soft and
independent: a failure in one never blocks the others, and the public API
always returns a list (never raises).

Public API:
    anomalies(window=150) -> list[{"kind","severity","text","layer"(optional)}]
    summary()             -> {"count": int, "anomalies": [...]}
"""
from __future__ import annotations

import time

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy is a hard dep but stay safe
    np = None


def _epoch(ts):
    """Parse a mesh ISO timestamp to epoch seconds; default to now on error."""
    try:
        return time.mktime(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return time.time()


def _fetch(window):
    """Pull recent insights, newest-first. Fail-soft to []."""
    try:
        from . import mesh
        rows = mesh.recent(n=int(window), layers=None, symbol="")
        return list(rows) if rows else []
    except Exception:
        return []


def _mean(vals):
    if not vals:
        return 0.0
    try:
        if np is not None:
            return float(np.mean(np.asarray(vals, dtype=float)))
        return float(sum(float(v) for v in vals) / len(vals))
    except Exception:
        return 0.0


def _salience(row):
    try:
        return float(row.get("salience", 0.0) or 0.0)
    except Exception:
        return 0.0


def _split(rows):
    """Newest ~30% vs older 70%. ``rows`` is newest-first.

    Returns (recent, older) where recent is the newest slice.
    """
    n = len(rows)
    k = max(1, int(round(n * 0.30)))
    k = min(k, n - 1) if n > 1 else n
    return rows[:k], rows[k:]


def _spike(rows, out):
    try:
        recent, older = _split(rows)
        r_mean = _mean([_salience(r) for r in recent])
        b_mean = _mean([_salience(r) for r in older])
        if b_mean > 0 and r_mean >= 1.5 * b_mean and r_mean >= 0.6:
            out.append({
                "kind": "salience_spike",
                "severity": "warn",
                "layer": "",
                "text": (
                    "Recent salience jumped to %.2f vs baseline %.2f "
                    "(%.1fx)" % (r_mean, b_mean, r_mean / b_mean)
                ),
            })
    except Exception:
        pass


def _silence(rows, out):
    try:
        n = len(rows)
        k = max(1, int(round(n * 0.30)))
        k = min(k, n - 1) if n > 1 else n
        recent = rows[:k]
        # "older half" = the older portion (everything not in the recent slice)
        older = rows[k:]
        recent_layers = {str(r.get("layer", "")) for r in recent}
        older_counts = {}
        for r in older:
            lay = str(r.get("layer", ""))
            if not lay:
                continue
            older_counts[lay] = older_counts.get(lay, 0) + 1
        for lay, cnt in sorted(older_counts.items()):
            if lay in recent_layers:
                continue
            sev = "warn" if cnt >= 5 else "info"
            out.append({
                "kind": "layer_silence",
                "severity": sev,
                "layer": lay,
                "text": (
                    "Layer '%s' was active (%d insights) but has gone "
                    "silent recently" % (lay, cnt)
                ),
            })
    except Exception:
        pass


def _burst(rows, out):
    try:
        if not rows:
            return
        # bucket counts per hour (epoch // 3600)
        buckets = {}
        for r in rows:
            h = int(_epoch(r.get("ts")) // 3600)
            buckets[h] = buckets.get(h, 0) + 1
        if len(buckets) < 2:
            return
        hours = sorted(buckets.keys())
        latest = hours[-1]
        recent_count = buckets[latest]
        prior = [buckets[h] for h in hours[:-1]]
        avg_prior = _mean(prior)
        if avg_prior > 0 and recent_count >= 5 and recent_count >= 2 * avg_prior:
            out.append({
                "kind": "volume_burst",
                "severity": "info",
                "layer": "",
                "text": (
                    "Volume burst: %d insights in the last hour vs avg %.1f/hr"
                    % (recent_count, avg_prior)
                ),
            })
    except Exception:
        pass


def anomalies(window: int = 150) -> list:
    """Detect anomalies over the most recent ``window`` insights.

    Returns [] when fewer than 8 insights are available, or on any failure.
    """
    out = []
    try:
        rows = _fetch(window)
        if len(rows) < 8:
            return []
        _spike(rows, out)
        _silence(rows, out)
        _burst(rows, out)
    except Exception:
        return out
    return out


def summary() -> dict:
    """Wrap :func:`anomalies` with a count for dashboard convenience."""
    try:
        a = anomalies()
    except Exception:
        a = []
    return {"count": len(a), "anomalies": a}
