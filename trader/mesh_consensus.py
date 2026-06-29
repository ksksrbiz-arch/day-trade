"""Cross-layer consensus engine for the insight mesh.

Reads recent insights from the shared mesh and surfaces the symbols that
multiple *independent* layers agree on -- and which direction they lean.

Consensus requires more than one voice: a symbol is only reported when at
least two distinct layers have weighed in with at least two mentions total.

Everything here is fail-soft: any failure reading the mesh yields safe,
empty defaults so callers (API endpoints, dashboards) never blow up.

Public API
----------
consensus(window=300) -> dict
    {"symbols": [{"symbol", "mentions", "layers", "net", "direction",
                  "agree", "salience"}], "generated": iso}
top(n=5) -> list
    The first n entries of consensus()["symbols"].
"""
from __future__ import annotations

from datetime import datetime, timezone

try:  # fail-soft import; mesh should always be present in-repo
    from . import mesh
except Exception:  # pragma: no cover - defensive
    mesh = None  # type: ignore

_ISO = "%Y-%m-%dT%H:%M:%SZ"

# Directional cue lexicons. Order does not matter; presence as a substring
# (token-ish) drives a +1 / -1 vote. "+" and "-" are explicit nudges.
_BULL = (
    "up", "long", "buy", "bull", "breakout", "risk_on",
    "rally", "gain", "surge", "+",
)
_BEAR = (
    "down", "short", "sell", "bear", "risk_off", "drop",
    "fall", "breakdown", "-",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_ISO)


def _direction_of(text: str) -> int:
    """Infer a directional vote from free text: +1 bullish, -1 bearish, 0 flat."""
    try:
        t = (text or "").lower()
    except Exception:
        return 0
    bull = any(cue in t for cue in _BULL)
    bear = any(cue in t for cue in _BEAR)
    if bull and not bear:
        return 1
    if bear and not bull:
        return -1
    return 0


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def consensus(window: int = 300) -> dict:
    """Aggregate recent mesh insights into per-symbol cross-layer consensus."""
    out: dict = {"symbols": [], "generated": _now_iso()}
    try:
        rows = mesh.recent(window) if mesh is not None else []
    except Exception:
        rows = []
    if not rows:
        return out

    # symbol -> accumulator
    acc: dict = {}
    for r in rows:
        try:
            sym = (r.get("symbol") or "").strip().upper()
            if not sym:
                continue
            layer = (r.get("layer") or "").strip() or "?"
            try:
                sal = float(r.get("salience") or 0.0)
            except Exception:
                sal = 0.0
            if sal < 0.0:
                sal = 0.0
            d = _direction_of(r.get("text") or "")
        except Exception:
            continue

        a = acc.setdefault(sym, {
            "mentions": 0,
            "wsum": 0.0,      # sum(dir * salience)
            "salsum": 0.0,    # sum(salience)
            "salience": 0.0,  # total salience (== salsum, kept explicit)
            # per-layer accumulators for the agree-count
            "layers": {},     # layer -> {"wsum", "salsum"}
        })
        a["mentions"] += 1
        a["wsum"] += d * sal
        a["salsum"] += sal
        a["salience"] += sal
        la = a["layers"].setdefault(layer, {"wsum": 0.0, "salsum": 0.0})
        la["wsum"] += d * sal
        la["salsum"] += sal

    symbols = []
    for sym, a in acc.items():
        layers = list(a["layers"].keys())
        # Consensus needs multiple voices.
        if a["mentions"] < 2 or len(layers) < 2:
            continue

        salsum = a["salsum"]
        net = (a["wsum"] / salsum) if salsum > 0 else 0.0
        if net > 0.15:
            direction = "up"
        elif net < -0.15:
            direction = "down"
        else:
            direction = "flat"

        net_sign = _sign(net)
        if net_sign == 0:
            agree = 0
        else:
            agree = 0
            for la in a["layers"].values():
                # each layer's own aggregated directional sign
                lnet = (la["wsum"] / la["salsum"]) if la["salsum"] > 0 else 0.0
                if _sign(lnet) == net_sign:
                    agree += 1

        symbols.append({
            "symbol": sym,
            "mentions": a["mentions"],
            "layers": sorted(layers),
            "net": round(net, 4),
            "direction": direction,
            "agree": agree,
            "salience": round(a["salience"], 4),
        })

    symbols.sort(key=lambda s: abs(s["net"]) * s["mentions"], reverse=True)
    out["symbols"] = symbols
    return out


def top(n: int = 5) -> list:
    """Return the top-n consensus symbols (first n of consensus()['symbols'])."""
    try:
        syms = consensus()["symbols"]
    except Exception:
        return []
    try:
        n = int(n)
    except Exception:
        n = 5
    if n < 0:
        n = 0
    return syms[:n]
