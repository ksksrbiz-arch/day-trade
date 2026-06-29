"""Mesh narrative synthesizer -- a plain-English read on the mesh's mind.

Stitches together the three mesh-intel sources (consensus, themes, anomalies)
into a short, deterministic, template-based situation paragraph describing what
the insight mesh is collectively "thinking" right now. No model, no network --
just readable sentences built from structured inputs.

Everything is fail-soft: each source is pulled inside its own try/except, so a
broken or empty source simply omits its sentence rather than raising. The public
API always returns a well-formed dict.

Public API:
    narrative() -> {"text": str, "generated": iso,
                    "parts": {"consensus": int, "themes": int, "anomalies": int}}
"""
from __future__ import annotations

import time

# Severity ranking so we can name the *most* severe anomaly.
_SEVERITY_RANK = {"critical": 3, "error": 3, "warn": 2, "warning": 2, "info": 1}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _consensus_sentence(parts: dict):
    """Top 1-3 consensus symbols with direction + agreeing-layer counts."""
    try:
        from . import mesh_consensus
        data = mesh_consensus.consensus(window=300) or {}
        symbols = data.get("symbols") or []
    except Exception:
        return None

    parts["consensus"] = len(symbols)
    if not symbols:
        return "No multi-layer consensus right now."

    bits = []
    for s in symbols[:3]:
        try:
            sym = str(s.get("symbol") or "?").upper()
            direction = str(s.get("direction") or "flat")
            agree = int(s.get("agree") or 0)
        except Exception:
            continue
        layer_word = "layer" if agree == 1 else "layers"
        bits.append("%s leaning %s (%d %s agree)" % (sym, direction, agree, layer_word))

    if not bits:
        return "No multi-layer consensus right now."

    if len(bits) == 1:
        body = bits[0]
    elif len(bits) == 2:
        body = bits[0] + " and " + bits[1]
    else:
        body = ", ".join(bits[:-1]) + ", and " + bits[-1]
    return "The mesh is converging on " + body + "."


def _themes_sentence(parts: dict):
    """List the top ~4 recurring theme terms. Skip (None) if none."""
    try:
        from . import mesh_themes
        data = mesh_themes.themes(window=200, top=8) or {}
        rows = data.get("themes") or []
    except Exception:
        return None

    terms = []
    for t in rows:
        try:
            term = str(t.get("term") or "").strip()
        except Exception:
            continue
        if term:
            terms.append(term)

    parts["themes"] = len(terms)
    if not terms:
        return None

    top = terms[:4]
    if len(top) == 1:
        listed = top[0]
    else:
        listed = ", ".join(top[:-1]) + " and " + top[-1]
    return "Recurring talk centers on " + listed + "."


def _anomalies_sentence(parts: dict):
    """Summarize anomaly count plus the most severe one."""
    try:
        from . import mesh_anomaly
        rows = mesh_anomaly.anomalies(window=150) or []
    except Exception:
        return None

    rows = [r for r in rows if isinstance(r, dict)]
    parts["anomalies"] = len(rows)
    if not rows:
        return "No anomalies detected."

    def _rank(r):
        try:
            return _SEVERITY_RANK.get(str(r.get("severity") or "").lower(), 0)
        except Exception:
            return 0

    worst = max(rows, key=_rank)
    try:
        worst_text = str(worst.get("text") or "").strip()
    except Exception:
        worst_text = ""

    n = len(rows)
    noun = "anomaly" if n == 1 else "anomalies"
    if worst_text:
        return "%d %s flagged; most notable: %s." % (n, noun, worst_text.rstrip("."))
    return "%d %s flagged." % (n, noun)


def narrative() -> dict:
    """Compose a short situation paragraph describing the mesh's collective view.

    Returns {"text", "generated", "parts": {consensus, themes, anomalies}}.
    Each contributing source is wrapped so a failure just omits its sentence.
    """
    parts = {"consensus": 0, "themes": 0, "anomalies": 0}
    sentences = []

    for builder in (_consensus_sentence, _themes_sentence, _anomalies_sentence):
        try:
            s = builder(parts)
        except Exception:
            s = None
        if s:
            sentences.append(s)

    text = " ".join(sentences).strip()
    return {"text": text, "generated": _now(), "parts": parts}
