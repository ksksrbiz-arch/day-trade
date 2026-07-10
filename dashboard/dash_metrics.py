"""Ledger reading + scoreboard aggregation for the dashboard (pure, no network)."""
from __future__ import annotations

import csv
import glob
import os
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent


def ledger_paths(bot_id: str | None = None) -> list[str]:
    if bot_id:
        return [str(PROJ / "data" / "bots" / bot_id / "trades.csv")]
    paths = [str(PROJ / "data" / "trades.csv")]
    paths += glob.glob(str(PROJ / "data" / "bots" / "*" / "trades.csv"))
    return paths


def read_ledger(bot_id: str | None = None, limit: int | None = None) -> list[dict]:
    rows: list[dict] = []
    for p in ledger_paths(bot_id):
        if not os.path.exists(p):
            continue
        try:
            with open(p, newline="") as f:
                for r in csv.DictReader(f):
                    r["_ledger"] = os.path.basename(os.path.dirname(p)) or "main"
                    rows.append(r)
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return rows[:limit] if limit else rows


def _f(row, key, default=0.0):
    try:
        return float(row.get(key, "") or default)
    except (TypeError, ValueError):
        return default


def summary(bot_id: str | None = None) -> dict:
    rows = read_ledger(bot_id)
    actions: dict[str, int] = {}
    events: dict[str, int] = {}
    sides = {"buy": 0, "sell": 0}
    confs, sents = [], []
    reasons: dict[str, int] = {}
    for r in rows:
        a = r.get("action", "")
        actions[a] = actions.get(a, 0) + 1
        if a in ("skip_unconfirmed", "skip_confluence", "skip"):
            rsn = (r.get("gate_reason", "") or r.get("reason", "") or "?")[:60]
            reasons[rsn] = reasons.get(rsn, 0) + 1
        if a in ("order", "order_failed"):
            ev = r.get("event", "") or "?"
            events[ev] = events.get(ev, 0) + 1
            s = r.get("side", "")
            if s in sides:
                sides[s] += 1
            confs.append(_f(r, "confidence"))
            sents.append(_f(r, "sentiment"))
    orders = actions.get("order", 0)
    total = len(rows)
    return {
        "total_decisions": total,
        "orders": orders,
        "skips": actions.get("skip", 0),
        "skips_unconfirmed": actions.get("skip_unconfirmed", 0),
        "order_failed": actions.get("order_failed", 0),
        "by_action": actions,
        "by_reason": dict(sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:12]),
        "by_event": events,
        "by_side": sides,
        "avg_confidence": round(sum(confs) / len(confs), 3) if confs else 0,
        "avg_sentiment": round(sum(sents) / len(sents), 3) if sents else 0,
    }
