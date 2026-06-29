"""Mesh digest -- a human-readable markdown snapshot of the mesh's state.

Composes the four mesh-intel sources (narrative, consensus, anomalies, SLA)
into a single, clean markdown document and writes it to disk for at-a-glance
review. No model, no network -- just structured inputs stitched into sections.

Everything is fail-soft: each sibling source is imported lazily inside its own
try/except, so a broken, missing, or empty source simply yields a placeholder
section ("_none_" / "_all layers nominal_") rather than raising. The disk
helpers swallow errors too, returning "" on failure.

Public API:
    build()              -> str   : the markdown text (does NOT write).
    write(path=None)     -> str   : build() then write; returns path or "".
    latest()             -> str   : read back data/digests/mesh_latest.md or "".
"""
from __future__ import annotations

import os
import time

_DEFAULT_LATEST = os.path.join("data", "digests", "mesh_latest.md")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M", time.gmtime())


# ---------------------------------------------------------------- sections ---
def _situation() -> str:
    try:
        from . import mesh_narrative
        data = mesh_narrative.narrative() or {}
        text = (data.get("text") or "").strip()
    except Exception:
        text = ""
    return text or "_no narrative available_"


def _consensus_lines() -> list:
    try:
        from . import mesh_consensus
        data = mesh_consensus.consensus(300) or {}
        symbols = data.get("symbols") or []
    except Exception:
        symbols = []

    lines = []
    for s in symbols:
        try:
            sym = s.get("symbol", "?")
            d = s.get("direction")
            if isinstance(d, (int, float)):
                dir_txt = "up" if d > 0 else "down" if d < 0 else "flat"
            else:
                dir_txt = str(d) if d else "flat"
            agree = s.get("agree")
            layers = s.get("layers") or []
            n_layers = len(layers) if isinstance(layers, (list, tuple)) else layers
            if agree is None:
                agree = n_layers
            lines.append(f"- **{sym}** {dir_txt} ({agree}/{n_layers} layers)")
        except Exception:
            continue
    return lines


def _anomaly_lines() -> list:
    try:
        from . import mesh_anomaly
        rows = mesh_anomaly.anomalies(150) or []
    except Exception:
        rows = []

    lines = []
    for a in rows:
        try:
            kind = a.get("kind", "anomaly")
            sev = a.get("severity", "")
            text = (a.get("text") or "").strip()
            prefix = f"{kind}"
            if sev:
                prefix += f"/{sev}"
            lines.append(f"- **{prefix}** {text}".rstrip())
        except Exception:
            continue
    return lines


def _sla_lines() -> list:
    try:
        from . import mesh_sla
        rows = mesh_sla.overdue() or []
    except Exception:
        rows = []

    lines = []
    for r in rows:
        try:
            layer = r.get("layer", "?")
            status = r.get("status", "?")
            last = r.get("last_seen_min")
            if last is None:
                lines.append(f"- **{layer}** -- {status}")
            else:
                lines.append(f"- **{layer}** -- {status} (last seen {last} min ago)")
        except Exception:
            continue
    return lines


# ------------------------------------------------------------------ build ---
def build() -> str:
    """Return the markdown digest text. Never raises; never writes."""
    try:
        out = []
        out.append(f"# Mesh Digest -- {_now_iso()}")
        out.append("")

        out.append("## Situation")
        out.append(_situation())
        out.append("")

        out.append("## Consensus")
        cons = _consensus_lines()
        out.extend(cons if cons else ["_none_"])
        out.append("")

        out.append("## Anomalies")
        anom = _anomaly_lines()
        out.extend(anom if anom else ["_none_"])
        out.append("")

        out.append("## Layer SLA")
        sla = _sla_lines()
        out.extend(sla if sla else ["_all layers nominal_"])
        out.append("")

        return "\n".join(out)
    except Exception:
        # Absolute fail-soft floor: still valid markdown.
        return f"# Mesh Digest -- {_now_iso()}\n\n_digest unavailable_\n"


def write(path: str | None = None) -> str:
    """build() then write to `path` (default data/digests/mesh_latest.md),
    creating parent dirs; also write a timestamped copy alongside it.
    Return the path written, or "" on failure."""
    try:
        target = path or _DEFAULT_LATEST
        text = build()

        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)

        # Timestamped copy in the same directory as the target.
        try:
            stamped = os.path.join(parent or ".", f"mesh_{_stamp()}.md")
            with open(stamped, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:
            pass

        return target
    except Exception:
        return ""


def latest() -> str:
    """Read back the default latest digest, or "" if absent/unreadable."""
    try:
        with open(_DEFAULT_LATEST, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""
