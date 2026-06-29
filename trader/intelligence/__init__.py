"""Intelligence layer — a clean namespace facade over the insight-mesh family.

PHASE 0 of the clean-architecture migration (see ARCHITECTURE.md). This package
is ADDITIVE and changes no behaviour: it simply re-exports the existing flat
``trader.mesh_*`` modules under a cohesive, layered namespace so call-sites can
start importing the clean path::

    from trader.intelligence import consensus, anomaly, themes, priority, sla

while the legacy imports (``from trader import mesh_consensus``) keep working
unchanged. When call-sites have migrated, the flat modules can move physically
into this package and the old names become thin shims, with the suite green at
every step. Importing this package pulls in only already-loaded siblings, so it
adds no new dependencies and cannot alter runtime behaviour.
"""
from __future__ import annotations

from .. import (
    mesh as bus,
    mesh_consensus as consensus,
    mesh_anomaly as anomaly,
    mesh_themes as themes,
    mesh_priority as priority,
    mesh_signal as signal,
    mesh_sla as sla,
    mesh_narrative as narrative,
    mesh_correlation as correlation,
    mesh_digest as digest,
    mesh_search as search,
    mesh_gc as gc,
)

__all__ = [
    "bus", "consensus", "anomaly", "themes", "priority", "signal",
    "sla", "narrative", "correlation", "digest", "search", "gc",
]
