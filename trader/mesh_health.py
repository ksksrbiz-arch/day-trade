"""Composite mesh-health score -- a single 0..100 gauge of mesh wellbeing.

A thin analytics layer that fuses several mesh-introspection sources into one
number a human (or a daemon) can glance at. Higher is healthier.

The score blends four 0..100 sub-scores:
    activity   (weight .25) -- is the mesh actually producing insights?
    diversity  (weight .25) -- are many distinct layers contributing?
    sla        (weight .30) -- are layers publishing on cadence (few stale/slow)?
    anomaly    (weight .20) -- is the mesh free of flagged anomalies?

Public API:
    score()   -> {"score": int(0..100), "grade": "A".."F",
                  "components": {"activity": int, "diversity": int,
                                 "sla": int, "anomaly": int},
                  "generated": iso}
    summary() -> str   one-line human summary, e.g.
                  "Mesh health 82/100 (B): 1 stale layer, 0 anomalies."

Every source is imported lazily inside its own try/except so a single failing
or missing sibling module degrades that sub-score gracefully (neutral fallback)
rather than taking down the whole report. Nothing here raises.
"""
from __future__ import annotations

import datetime

# Sub-score weights (sum to 1.0).
_W_ACTIVITY = 0.25
_W_DIVERSITY = 0.25
_W_SLA = 0.30
_W_ANOMALY = 0.20

# Expected number of distinct contributing layers for full diversity credit.
_EXPECTED_LAYERS = 10


def _now() -> str:
    """ISO-8601 UTC timestamp matching the mesh's own format."""
    try:
        from . import mesh
        return mesh._now()
    except Exception:  # noqa: BLE001
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(v) -> int:
    """Coerce to an int in [0, 100], fail-soft to 0."""
    try:
        n = int(round(float(v)))
    except Exception:  # noqa: BLE001
        return 0
    if n < 0:
        return 0
    if n > 100:
        return 100
    return n


def _activity_score() -> int:
    """Insight volume in mesh.recent(200): min(100, count/2).

    No recent activity -> 0 (unhealthy). Defaults to 0 on any failure.
    """
    try:
        from . import mesh
    except Exception:  # noqa: BLE001
        return 0
    try:
        rows = mesh.recent(n=200)
    except Exception:  # noqa: BLE001
        return 0
    try:
        count = len(rows) if rows else 0
    except Exception:  # noqa: BLE001
        return 0
    return _clamp(min(100.0, count / 2.0))


def _diversity_score() -> int:
    """distinct layers / expected (~10) * 100, capped at 100.

    Defaults to 0 on any failure (no layers seen -> unhealthy).
    """
    try:
        from . import mesh
    except Exception:  # noqa: BLE001
        return 0
    try:
        rows = mesh.recent(n=200)
    except Exception:  # noqa: BLE001
        return 0
    try:
        layers = set()
        for r in (rows or []):
            try:
                lay = r.get("layer")
            except Exception:  # noqa: BLE001
                continue
            if lay:
                layers.add(lay)
        distinct = len(layers)
    except Exception:  # noqa: BLE001
        return 0
    return _clamp(distinct / float(_EXPECTED_LAYERS) * 100.0)


def _sla_score() -> int:
    """100 minus 25 per stale layer and 10 per slow layer, floored at 0.

    A missing/failing mesh_sla source is treated as neutral (100) so it does
    not unfairly punish the composite when SLA info is simply unavailable.
    """
    try:
        from . import mesh_sla
    except Exception:  # noqa: BLE001
        return 100
    try:
        rows = mesh_sla.overdue()
    except Exception:  # noqa: BLE001
        return 100
    try:
        stale = slow = 0
        for r in (rows or []):
            try:
                status = r.get("status")
            except Exception:  # noqa: BLE001
                continue
            if status == "stale":
                stale += 1
            elif status == "slow":
                slow += 1
    except Exception:  # noqa: BLE001
        return 100
    return _clamp(100.0 - 25.0 * stale - 10.0 * slow)


def _anomaly_score() -> int:
    """100 minus 20 per warn anomaly and 7 per info anomaly, floored at 0.

    A missing/failing mesh_anomaly source is treated as neutral (100).
    """
    try:
        from . import mesh_anomaly
    except Exception:  # noqa: BLE001
        return 100
    try:
        rows = mesh_anomaly.anomalies(150)
    except Exception:  # noqa: BLE001
        return 100
    try:
        warn = info = 0
        for r in (rows or []):
            try:
                sev = r.get("severity")
            except Exception:  # noqa: BLE001
                continue
            if sev == "warn":
                warn += 1
            elif sev == "info":
                info += 1
    except Exception:  # noqa: BLE001
        return 100
    return _clamp(100.0 - 20.0 * warn - 7.0 * info)


def _grade(score: int) -> str:
    """Letter grade for a 0..100 score."""
    try:
        s = int(score)
    except Exception:  # noqa: BLE001
        s = 0
    if s >= 90:
        return "A"
    if s >= 80:
        return "B"
    if s >= 70:
        return "C"
    if s >= 60:
        return "D"
    return "F"


def score() -> dict:
    """Composite mesh-health score and component breakdown.

    Returns ``{"score", "grade", "components", "generated"}``.
    """
    activity = _activity_score()
    diversity = _diversity_score()
    sla = _sla_score()
    anomaly = _anomaly_score()

    try:
        composite = (
            _W_ACTIVITY * activity
            + _W_DIVERSITY * diversity
            + _W_SLA * sla
            + _W_ANOMALY * anomaly
        )
    except Exception:  # noqa: BLE001
        composite = 0
    total = _clamp(composite)

    return {
        "score": total,
        "grade": _grade(total),
        "components": {
            "activity": activity,
            "diversity": diversity,
            "sla": sla,
            "anomaly": anomaly,
        },
        "generated": _now(),
    }


def _count_stale() -> int:
    """Number of stale layers per mesh_sla.overdue(); 0 on any failure."""
    try:
        from . import mesh_sla
        rows = mesh_sla.overdue()
    except Exception:  # noqa: BLE001
        return 0
    try:
        return sum(1 for r in (rows or []) if (r or {}).get("status") == "stale")
    except Exception:  # noqa: BLE001
        return 0


def _count_anomalies() -> int:
    """Total anomalies per mesh_anomaly.anomalies(150); 0 on any failure."""
    try:
        from . import mesh_anomaly
        rows = mesh_anomaly.anomalies(150)
    except Exception:  # noqa: BLE001
        return 0
    try:
        return len(rows) if rows else 0
    except Exception:  # noqa: BLE001
        return 0


def summary() -> str:
    """One-line human summary of mesh health."""
    try:
        s = score()
        total = s.get("score", 0)
        grade = s.get("grade", "F")
    except Exception:  # noqa: BLE001
        total, grade = 0, "F"

    stale = _count_stale()
    anoms = _count_anomalies()

    stale_word = "layer" if stale == 1 else "layers"
    anom_word = "anomaly" if anoms == 1 else "anomalies"
    return (
        "Mesh health %d/100 (%s): %d stale %s, %d %s."
        % (total, grade, stale, stale_word, anoms, anom_word)
    )
