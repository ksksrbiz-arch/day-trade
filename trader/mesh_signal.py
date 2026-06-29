"""Turn cross-layer mesh consensus into a tradeable signal.

The insight mesh's :mod:`mesh_consensus` engine surfaces which symbols multiple
independent layers agree on and which direction they lean. This module distills
that consensus into a single scalar signal in ``[-1, 1]`` that the confluence
brain (and other downstream consumers) can fold into its decision making.

The signal blends *direction/strength* (``net``) with *confidence*, where
confidence grows as more layers agree (breadth) and as more mentions pile up
(depth). A symbol that only barely clears the consensus bar produces a muted
signal; a symbol with broad, repeated agreement produces a signal close to its
raw ``net``.

Everything here is fail-soft: any failure reading consensus yields safe,
empty/``None`` defaults so callers never blow up.

Public API
----------
consensus_signal(symbol, window=300) -> float | None
    Signal in [-1, 1] for one symbol, or None when there is not enough
    consensus (fewer than 2 layers agree) or the symbol is absent.
signals(window=300) -> dict
    {"signals": {SYM: float, ...}, "generated": iso} for all consensus symbols
    that produce a (non-None) signal.
"""
from __future__ import annotations

from datetime import datetime, timezone

try:  # fail-soft import; sibling should always be present in-repo
    from . import mesh_consensus
except Exception:  # pragma: no cover - defensive
    mesh_consensus = None  # type: ignore

_ISO = "%Y-%m-%dT%H:%M:%SZ"

# Minimum number of layers that must agree on the consensus direction.
_MIN_AGREE = 2
# Scaling denominators for the confidence factors.
_AGREE_FULL = 3.0     # agree >= 3 layers -> full breadth confidence
_MENTIONS_FULL = 4.0  # mentions >= 4 -> full depth confidence


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_ISO)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _signal_from_entry(entry: dict):
    """Compute a signal in [-1, 1] from one consensus symbol entry.

    Returns None when fewer than ``_MIN_AGREE`` layers agree (not enough
    consensus) or the entry is malformed.
    """
    try:
        agree = int(entry.get("agree") or 0)
    except Exception:
        return None
    if agree < _MIN_AGREE:
        return None

    try:
        net = float(entry.get("net") or 0.0)
    except Exception:
        return None
    try:
        mentions = int(entry.get("mentions") or 0)
    except Exception:
        mentions = 0

    breadth = min(1.0, agree / _AGREE_FULL)
    depth = min(1.0, mentions / _MENTIONS_FULL)
    confidence = breadth * depth

    sig = _clamp(net * confidence)
    return round(sig, 4)


def consensus_signal(symbol: str, window: int = 300):
    """Return a tradeable signal in [-1, 1] for ``symbol``, or None.

    The symbol is matched case-insensitively against the consensus output. If
    the symbol is absent, or fewer than two layers agree, ``None`` is returned.
    """
    try:
        sym = (symbol or "").strip().upper()
    except Exception:
        return None
    if not sym:
        return None

    try:
        data = mesh_consensus.consensus(window) if mesh_consensus is not None else {}
        rows = data.get("symbols") or []
    except Exception:
        return None

    for entry in rows:
        try:
            if (entry.get("symbol") or "").strip().upper() == sym:
                return _signal_from_entry(entry)
        except Exception:
            continue
    return None


def signals(window: int = 300) -> dict:
    """Map every consensus symbol to its signal, skipping None.

    Returns ``{"signals": {SYM: float, ...}, "generated": iso}``.
    """
    out: dict = {"signals": {}, "generated": _now_iso()}
    try:
        data = mesh_consensus.consensus(window) if mesh_consensus is not None else {}
        rows = data.get("symbols") or []
    except Exception:
        return out

    for entry in rows:
        try:
            sym = (entry.get("symbol") or "").strip().upper()
            if not sym:
                continue
            sig = _signal_from_entry(entry)
            if sig is None:
                continue
            out["signals"][sym] = sig
        except Exception:
            continue
    return out
