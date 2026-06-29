"""Unified activity timeline -- one chronological tape of everything the platform
does. Merges the insight mesh, executed trades, news catalysts, agent traces, and
autonomy actions into a single time-sorted stream the dashboard renders as a
modern event timeline.

Each event: {ts, iso, kind, source, text, symbol, tone}  (tone -1/0/+1).
Fail-soft per source; trades are passed in by the API layer (which owns the
ledger reader) via `extra`.
"""
from __future__ import annotations

import time

_KINDS = ("trade", "news", "agent", "autonomy", "mesh", "prediction", "shadow", "edge")


def _to_epoch(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return time.mktime(time.strptime(v[:19], fmt))
            except Exception:  # noqa: BLE001
                continue
    return time.time()


def _norm(ts, kind, text, source="", symbol="", tone=0) -> dict:
    e = _to_epoch(ts)
    return {"ts": e, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e)),
            "kind": kind, "source": source, "text": (text or "")[:200],
            "symbol": (symbol or "").upper(), "tone": int(max(-1, min(1, tone)))}


def _sign(x) -> int:
    try:
        x = float(x)
    except Exception:  # noqa: BLE001
        return 0
    return 1 if x > 0.02 else -1 if x < -0.02 else 0


def events(limit: int = 60, extra: list[dict] | None = None) -> list[dict]:
    evs: list[dict] = []

    # mesh insights (layer-tagged)
    try:
        from . import mesh
        for r in mesh.recent(30):
            layer = r.get("layer", "mesh")
            evs.append(_norm(r.get("ts"), "mesh", r.get("text"), layer, r.get("symbol", "")))
    except Exception:  # noqa: BLE001
        pass

    # news catalysts (top, sentiment-toned)
    try:
        from . import newshub
        for it in newshub.aggregate().get("items", [])[:14]:
            evs.append(_norm(it.get("ts"), "news", it.get("title"), it.get("source", "news"),
                             (it.get("symbols") or [""])[0] if it.get("symbols") else "",
                             _sign(it.get("sentiment", 0))))
    except Exception:  # noqa: BLE001
        pass

    # agent traces
    try:
        from .agents import state
        for tr in state.recent_traces(20):
            tone = -1 if tr.get("status") == "failed" else 0
            txt = f"{tr.get('agent','?')} · {tr.get('tool','')}: {tr.get('summary','')}"
            evs.append(_norm(tr.get("ts"), "agent", txt, tr.get("agent", ""), tone=tone))
    except Exception:  # noqa: BLE001
        pass

    # autonomy audit
    try:
        from . import autonomy
        for a in autonomy.recent_audit(12):
            tone = 1 if a.get("status") == "applied" else 0
            evs.append(_norm(a.get("ts"), "autonomy", f"{a.get('action')}: {a.get('reason','')}",
                             a.get("status", ""), tone=tone))
    except Exception:  # noqa: BLE001
        pass

    # extra (trades, supplied by the API which owns the ledger)
    for x in (extra or []):
        evs.append(_norm(x.get("ts"), x.get("kind", "trade"), x.get("text"),
                         x.get("source", ""), x.get("symbol", ""), x.get("tone", 0)))

    evs.sort(key=lambda e: e["ts"], reverse=True)
    return evs[:limit]


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    for e in events(20):
        print(f"{e['iso']}  [{e['kind']:9}] {e['text'][:80]}")
