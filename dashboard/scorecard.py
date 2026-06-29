"""
Run scorecard -- score what the system DID against a business rubric, not just
generic correctness:

  * accuracy        -- orders that executed cleanly vs failed; gate selectivity
  * profit_impact   -- realized P&L (passed in from Alpaca by the endpoint)
  * reversibility   -- avg reversibility of actions (paper equity 1.0, short 0.7,
                       option 0.5) -- safer actions score higher
  * time_saved      -- decisions automated (each a judgment a human didn't make)

These feed a 0-100 composite so the system can learn what "good" means HERE.
Pure over the ledger; realized P&L is injected so this stays network-free.
"""
from __future__ import annotations

from dashboard import dash_metrics as M


def _reversibility(row) -> float:
    if (row.get("instrument") or "") == "option":
        return 0.5
    if (row.get("side") or "") == "sell" and (row.get("instrument") or "equity") == "equity":
        return 0.7
    return 1.0


def score(bot_id=None, realized_pl: float = 0.0) -> dict:
    rows = M.read_ledger(bot_id)
    total = len(rows)
    orders = [r for r in rows if r.get("action") == "order"]
    failed = [r for r in rows if r.get("action") == "order_failed"]
    skips = [r for r in rows if str(r.get("action", "")).startswith("skip")]
    n_ord = len(orders)
    exec_accuracy = round(100 * n_ord / (n_ord + len(failed)), 1) if (n_ord + len(failed)) else 0.0
    selectivity = round(100 * len(skips) / total, 1) if total else 0.0
    rev = round(sum(_reversibility(r) for r in orders) / n_ord, 2) if n_ord else 1.0
    time_saved_min = round(total * 1.5, 0)   # ~1.5 min of human judgment per decision

    # composite 0-100: weight accuracy, reversibility, and profit sign; selectivity is a bonus
    profit_term = 50 + max(-50, min(50, realized_pl / 20.0))   # +/-$1000 -> +/-50
    comp = (0.30 * exec_accuracy + 0.20 * (rev * 100) + 0.40 * profit_term + 0.10 * selectivity)
    composite = round(max(0, min(100, comp)), 1)
    return {"n_decisions": total, "orders": n_ord, "failed": len(failed),
            "exec_accuracy": exec_accuracy, "selectivity": selectivity,
            "reversibility": rev, "realized_pl": round(realized_pl, 2),
            "time_saved_min": time_saved_min, "composite": composite}
