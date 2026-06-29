"""Mesh garbage-collection -- forgetting for the insight store.

The mesh accumulates insights forever; most decay into noise. To keep the graph
signal-rich (in the PTDNet / learned-sparsification spirit) we prune insights
that are BOTH stale (older than a TTL) AND low-salience. High-salience and recent
insights are never touched -- forgetting is conservative and asymmetric.

Public API:
  preview(ttl_days, min_salience) -> {"total","would_prune","kept","cutoff"}
      Read-only census of prune candidates. Modifies nothing.
  compact(ttl_days, min_salience, dry_run) -> {"pruned","remaining","dry_run"}
      dry_run=True  : behaves like preview (no DELETE).
      dry_run=False : DELETEs the candidate rows and reports remaining count.

Everything is fail-soft: on any error the functions return zeros rather than
raise, so a GC pass can never take down a caller.
"""
from __future__ import annotations

import time

from . import mesh

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _cutoff_epoch(ttl_days: float) -> float:
    """Epoch seconds before which an insight counts as stale."""
    return time.time() - float(ttl_days) * 86400.0


def _cutoff_iso(ttl_days: float) -> str:
    return time.strftime(_TS_FMT, time.gmtime(_cutoff_epoch(ttl_days)))


def _row_epoch(iso: str) -> float | None:
    """Parse a stored ts to epoch seconds; None if unparseable."""
    try:
        return time.mktime(time.strptime(iso, _TS_FMT))
    except Exception:  # noqa: BLE001
        return None


def _candidate_ids(c, ttl_days: float, min_salience: float):
    """IDs of insights older than ttl_days AND salience < min_salience.

    Filtering is done in Python (via parsed ts) so a malformed ts can never be
    mistaken for "stale" -- unparseable rows are kept.
    """
    cutoff = _cutoff_epoch(ttl_days)
    ids = []
    for r in c.execute("SELECT id, ts, salience FROM insights").fetchall():
        sal = r["salience"]
        if sal is None or float(sal) >= float(min_salience):
            continue
        ep = _row_epoch(r["ts"])
        if ep is None:
            continue
        if ep < cutoff:
            ids.append(r["id"])
    return ids


def preview(ttl_days: float = 14.0, min_salience: float = 0.5) -> dict:
    """Count prune candidates (stale AND low-salience) without modifying anything."""
    try:
        c = mesh.conn()
        try:
            total = c.execute("SELECT COUNT(*) AS n FROM insights").fetchone()["n"]
            would = len(_candidate_ids(c, ttl_days, min_salience))
        finally:
            c.close()
        return {"total": int(total), "would_prune": int(would),
                "kept": int(total) - int(would), "cutoff": _cutoff_iso(ttl_days)}
    except Exception:  # noqa: BLE001
        return {"total": 0, "would_prune": 0, "kept": 0, "cutoff": _cutoff_iso(ttl_days)}


def compact(ttl_days: float = 14.0, min_salience: float = 0.5,
            dry_run: bool = True) -> dict:
    """Prune stale low-salience insights.

    dry_run=True behaves like preview (no DELETE). dry_run=False DELETEs the
    candidate rows. Fail-soft: returns zeros on error.
    """
    try:
        c = mesh.conn()
        try:
            ids = _candidate_ids(c, ttl_days, min_salience)
            if dry_run:
                remaining = c.execute("SELECT COUNT(*) AS n FROM insights").fetchone()["n"]
                return {"pruned": len(ids), "remaining": int(remaining), "dry_run": True}
            pruned = 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                ph = ",".join("?" * len(chunk))
                cur = c.execute("DELETE FROM insights WHERE id IN (%s)" % ph, chunk)
                pruned += cur.rowcount
            c.commit()
            remaining = c.execute("SELECT COUNT(*) AS n FROM insights").fetchone()["n"]
            return {"pruned": int(pruned), "remaining": int(remaining), "dry_run": False}
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return {"pruned": 0, "remaining": 0, "dry_run": bool(dry_run)}
