"""Agents learn from outcomes.

Every council convocation is logged. `reconcile()` later replays those logged
votes against the realized forward return (from CRSP-lite prices) and updates a
rolling per-agent accuracy. `weights()` turns that accuracy into an influence
multiplier so council members who have been right gain say and members who have
been wrong lose it -- the free-AI agents literally learn their standing from the
market, paired with the ML layer that consumes the same outcomes.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "ml"))
LOG = os.path.join(_DATA, "council_log.jsonl")
REL = os.path.join(_DATA, "agent_reliability.json")
_VAL = {"bullish": 1, "bearish": -1, "neutral": 0}


def log_votes(symbol: str, side: str, members: list[dict]):
    """Append one convocation's member stances for later reconciliation."""
    try:
        os.makedirs(_DATA, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "day": time.strftime("%Y-%m-%d", time.gmtime()),
               "symbol": symbol.upper(), "side": side,
               "votes": [{"source": m.get("source"), "stance": m.get("stance")}
                         for m in members if m.get("stance")]}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def load_reliability() -> dict:
    if not os.path.exists(REL):
        return {}
    try:
        return json.load(open(REL))
    except Exception:  # noqa: BLE001
        return {}


def weights(floor: float = 0.4, cap: float = 1.8, min_n: int = 5) -> dict:
    """source -> influence multiplier. Unknown / thin history -> 1.0 (neutral)."""
    rel = load_reliability()
    out = {}
    for src, d in rel.items():
        if d.get("total", 0) < min_n:
            out[src] = 1.0
        else:
            acc = d.get("acc", 0.5)
            out[src] = max(floor, min(cap, 0.5 + acc))   # 50% acc -> 1.0
    return out


def _series(sym):
    """(date, close) history -- CRSP if populated, else Alpaca IEX daily bars."""
    try:
        from ..crsp import query as crsp
        bars = crsp.get_prices(sym, "2023-01-01", None)
        cl = [(b["date"], b["close"]) for b in bars if b.get("close")]
        if len(cl) >= 30:
            return cl
    except Exception:  # noqa: BLE001
        pass
    try:
        from .dataset import _alpaca_series
        return _alpaca_series(sym)
    except Exception:  # noqa: BLE001
        return []


def reconcile(horizon: int = 5, decay: float = 0.0) -> dict:
    """Score logged votes vs realized forward return; update accuracy. Returns summary."""
    if not os.path.exists(LOG):
        return {"reconciled": 0, "note": "no council log yet"}
    rel = load_reliability()
    done = 0
    processed = set()
    seen_path = LOG + ".done"
    if os.path.exists(seen_path):
        try:
            processed = set(json.load(open(seen_path)))
        except Exception:  # noqa: BLE001
            processed = set()
    lines = open(LOG, encoding="utf-8").read().splitlines()
    for ln in lines:
        if not ln.strip():
            continue
        h = str(hash(ln))
        if h in processed:
            continue
        try:
            rec = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        sym, day = rec.get("symbol"), rec.get("day")
        if not sym or "/" in sym or not day:
            processed.add(h); continue
        pairs = _series(sym)
        if not pairs:
            continue
        closes = [c for _, c in pairs]
        ds = [d for d, _ in pairs]
        idx = next((i for i, dd in enumerate(ds) if dd >= day), None)
        if idx is None or idx + horizon >= len(closes):
            continue                      # not matured yet -> leave for later
        fwd = closes[idx + horizon] / closes[idx] - 1.0 if closes[idx] else 0.0
        realized = 1 if fwd > 0 else -1
        for v in rec.get("votes", []):
            src, st = v.get("source"), _VAL.get(v.get("stance"))
            if src is None or st is None or st == 0:
                continue
            d = rel.setdefault(src, {"right": 0, "total": 0, "acc": 0.5})
            d["total"] += 1
            if st == realized:
                d["right"] += 1
            d["acc"] = round(d["right"] / d["total"], 4)
        processed.add(h); done += 1
    os.makedirs(_DATA, exist_ok=True)
    json.dump(rel, open(REL, "w"), indent=2)
    json.dump(list(processed)[-5000:], open(seen_path, "w"))
    return {"reconciled": done, "agents": {k: v["acc"] for k, v in rel.items()}}


if __name__ == "__main__":
    print("weights:", weights())
    print("reconcile:", reconcile())
