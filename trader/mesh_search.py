"""Mesh insight search -- ranked, substring search over recent insights.

A lightweight, dependency-free relevance search across the insight mesh: pull
recent insights, tokenize a free-text query, and score each insight by how many
distinct query tokens appear (as substrings) in its text or symbol, weighted by
the insight's salience and how recent it is. This gives the dashboard/awareness
layer a cheap, explainable "find me what the mesh said about X" without any
model or external index.

Scoring per insight:
    score = term_hits * (1 + salience) * recency
where
    term_hits = number of distinct query tokens found as substrings in the
                lowercased text (or symbol)
    recency   = 1 / (1 + age_hours / 12)
Only insights with term_hits >= 1 are kept.

Everything is fail-soft: a broken mesh, missing keys, odd text, or unparseable
timestamps never raise -- they just contribute zero / get skipped.

Public API:
  search(query, window=500, limit=20) ->
      {"query": str,
       "results": [{"id","layer","symbol","ts","salience","text","score"}],
       "generated": iso}
  count(query, window=500) -> int   # number of matching insights (term_hits>=1)
"""
from __future__ import annotations

import calendar
import re
import time

try:  # import must never hard-fail callers
    from . import mesh
except Exception:  # noqa: BLE001
    mesh = None  # type: ignore

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_epoch() -> float:
    return time.time()


def _tokenize(query: str):
    """Lowercase, split on non-alphanumerics, drop tokens shorter than 2 chars.
    Returns the distinct surviving tokens (order-insensitive)."""
    out = []
    seen = set()
    if not query:
        return out
    for tok in _TOKEN_SPLIT.split(str(query).lower()):
        if len(tok) < 2:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _parse_ts(ts: str):
    """Parse an ISO 'YYYY-MM-DDTHH:MM:SSZ' UTC timestamp to epoch seconds.
    Returns None on any failure (fail-soft)."""
    try:
        return calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return None


def _recency(ts: str, now_epoch: float) -> float:
    """recency = 1 / (1 + age_hours / 12). Unknown/odd timestamps -> 1.0
    (treated as 'now', i.e. no recency penalty)."""
    epoch = _parse_ts(ts)
    if epoch is None:
        return 1.0
    age_hours = (now_epoch - epoch) / 3600.0
    if age_hours < 0:
        age_hours = 0.0
    return 1.0 / (1.0 + age_hours / 12.0)


def _term_hits(tokens, text: str, symbol: str) -> int:
    """Number of distinct query tokens found as substrings in the lowercased
    text or symbol."""
    hay = ""
    try:
        hay = (str(text or "") + " " + str(symbol or "")).lower()
    except Exception:  # noqa: BLE001
        return 0
    hits = 0
    for tok in tokens:
        if tok in hay:
            hits += 1
    return hits


def _salience(val) -> float:
    try:
        return float(val)
    except Exception:  # noqa: BLE001
        return 0.0


def _scored(query: str, window: int):
    """Internal: yield (score, insight_dict) for matching insights."""
    tokens = _tokenize(query)
    if not tokens:
        return []
    if mesh is None:
        return []
    try:
        rows = mesh.recent(n=int(window))
    except Exception:  # noqa: BLE001
        return []
    if not rows:
        return []

    now_epoch = _now_epoch()
    out = []
    for row in rows:
        try:
            text = row.get("text", "")
            symbol = row.get("symbol", "")
            hits = _term_hits(tokens, text, symbol)
            if hits < 1:
                continue
            sal = _salience(row.get("salience", 0.0))
            rec = _recency(row.get("ts", ""), now_epoch)
            score = hits * (1.0 + sal) * rec
            out.append((score, row))
        except Exception:  # noqa: BLE001
            continue
    return out


def search(query: str, window: int = 500, limit: int = 20) -> dict:
    """Ranked substring search over recent insights. See module docstring."""
    scored = _scored(query, window)
    scored.sort(key=lambda pair: pair[0], reverse=True)

    results = []
    try:
        cap = int(limit)
    except Exception:  # noqa: BLE001
        cap = 20
    for score, row in scored[: max(cap, 0)]:
        results.append({
            "id": row.get("id"),
            "layer": row.get("layer"),
            "symbol": row.get("symbol"),
            "ts": row.get("ts"),
            "salience": _salience(row.get("salience", 0.0)),
            "text": row.get("text", ""),
            "score": round(float(score), 4),
        })

    return {
        "query": query if query is not None else "",
        "results": results,
        "generated": _now_iso(),
    }


def count(query: str, window: int = 500) -> int:
    """Number of insights matching the query (term_hits >= 1)."""
    return len(_scored(query, window))
