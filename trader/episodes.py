"""
episodes.py -- EPISODIC decision memory (distinct from beliefs = semantic memory).

Beliefs are what the system knows; episodes are what it DID. Each executed
decision is logged with the full context it was made in:

    {ts, regime, mood, valence, curiosity, symbol, side, entry, active_beliefs,
     outcome_ret, outcome_ts, resolved}

On the reflect cadence the system (a) resolves matured episodes against realized
price, (b) can retrieve past decisions made in a SIMILAR regime+mood state and
see the outcome distribution, and (c) forms SECOND-ORDER beliefs -- beliefs
about its own behaviour ("I overtrade in high-curiosity, low-calibration
states") rather than only about the market. Pure stdlib, fail-soft.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
LOG = PROJ / "data" / "episodes.jsonl"
_RESOLVE_BARS = 5              # trading days before an episode's outcome is scored


def log(symbol: str, side: str, entry: float, regime: str = "neutral",
        mood: str = "", valence: float = 0.0, curiosity: float = 0.0,
        active_beliefs: list | None = None) -> None:
    """Record an executed decision + the internal/market state it was made in."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "symbol": (symbol or "").upper(), "side": side,
               "entry": float(entry) if entry else None, "regime": regime,
               "mood": mood, "valence": round(float(valence), 3),
               "curiosity": round(float(curiosity), 3),
               "active_beliefs": (active_beliefs or [])[:6],
               "outcome_ret": None, "outcome_ts": None, "resolved": False}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _rows() -> list:
    if not LOG.exists():
        return []
    out = []
    for ln in open(LOG, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                pass
    return out


def _price(sym: str) -> float | None:
    try:
        from .ml.dataset import _alpaca_series
        s = _alpaca_series(sym)
        return float(s[-1][1]) if s else None
    except Exception:  # noqa: BLE001
        return None


def resolve() -> dict:
    """Score matured episodes (older than the horizon) against realized price."""
    rows = _rows()
    if not rows:
        return {"resolved": 0, "total": 0}
    now = time.time()
    changed = 0
    px_cache: dict = {}
    for r in rows:
        if r.get("resolved") or not r.get("entry"):
            continue
        if now - r.get("ts", now) < _RESOLVE_BARS * 86400:
            continue
        sym = r["symbol"]
        if sym not in px_cache:
            px_cache[sym] = _price(sym)
        cur = px_cache[sym]
        if not cur:
            continue
        entry = r["entry"]
        ret = (cur / entry - 1.0) if r.get("side") in ("buy", "long") else (entry / cur - 1.0)
        r["outcome_ret"] = round(ret, 4)
        r["outcome_ts"] = now
        r["resolved"] = True
        changed += 1
    if changed:
        try:
            with open(LOG, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        except Exception:  # noqa: BLE001
            pass
    return {"resolved": changed, "total": len(rows)}


def recall_similar(regime: str, mood: str, k: int = 20) -> dict:
    """Outcome distribution of past RESOLVED decisions made in a similar state."""
    res = [r for r in _rows() if r.get("resolved") and r.get("outcome_ret") is not None
           and r.get("regime") == regime and r.get("mood") == mood]
    res = res[-k:]
    if not res:
        return {"n": 0}
    rets = [r["outcome_ret"] for r in res]
    wins = sum(1 for x in rets if x > 0)
    return {"n": len(rets), "win_rate": round(wins / len(rets), 3),
            "avg_ret_pct": round(sum(rets) / len(rets) * 100, 3),
            "regime": regime, "mood": mood}


def behavior_stats() -> list:
    """Group resolved episodes by (mood) and by high/low curiosity to expose
    behavioural patterns the system can form second-order beliefs about."""
    res = [r for r in _rows() if r.get("resolved") and r.get("outcome_ret") is not None]
    if not res:
        return []
    groups: dict = {}
    for r in res:
        cur_band = "high-curiosity" if r.get("curiosity", 0) >= 0.55 else "low-curiosity"
        key = f"{r.get('mood', '?')} · {cur_band}"
        groups.setdefault(key, []).append(r["outcome_ret"])
    out = []
    for key, rets in groups.items():
        if len(rets) < 4:
            continue
        wins = sum(1 for x in rets if x > 0)
        out.append({"state": key, "n": len(rets), "win_rate": round(wins / len(rets), 3),
                    "avg_ret_pct": round(sum(rets) / len(rets) * 100, 3)})
    out.sort(key=lambda g: g["avg_ret_pct"])
    return out


def stats() -> dict:
    rows = _rows()
    res = [r for r in rows if r.get("resolved")]
    return {"total": len(rows), "resolved": len(res),
            "behavior": behavior_stats()[:6]}


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
