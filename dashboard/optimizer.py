"""
Daily auto-optimization engine -- a closed loop over the trading system.

  ingest (prior-day ledgers + realized P&L)
    -> normalize (Realized P&L per $1k deployed)
    -> detect anomalies (rolling z-score vs each bot's own history + hard thresholds)
    -> recommend (ranked by $ impact)
    -> auto-tune (PRE-APPROVED, BOUNDED, risk-reducing only)
    -> digest (markdown to data/digests/) + history snapshot

Honesty: "anomaly detection" here is statistical (z-scores + fixed thresholds),
not a black-box ML model -- appropriate for this data scale and fully auditable.
Auto-tuning only ever makes the system MORE conservative (higher cooldown/
confidence, or pause a persistent loser). It never increases risk, never sizes
up, never touches real money. The control bot 'baseline-v0' is never tuned.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, pstdev

PROJ = Path(__file__).resolve().parent.parent
DATA = PROJ / "data"
DIGESTS = DATA / "digests"
HISTORY = DATA / "opt_history.jsonl"
LOG = DATA / "optimizer.log"

# --- baselines / bounds (the "policy") ---
BASE = {
    "churn_max": 12,          # orders/day above this is over-trading
    "per1k_floor": -8.0,      # realized P&L per $1k below this is bleeding
    "failed_max": 8,          # order_failed/day above this -> flag
    "z_flag": 2.0,            # |z| of per1k vs own history to flag an anomaly
    "cooldown_step": 30, "cooldown_cap": 180,
    "conf_step": 0.05, "conf_cap": 0.70,
    "pause_after_days": 2,    # consecutive bleeding days -> pause
}
CONTROL = "baseline-v0"      # never auto-tuned


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def per_1k(realized: float, deployed: float) -> float:
    """Realized P&L normalized per $1,000 of capital deployed."""
    if deployed <= 0:
        return 0.0
    return round(realized / (deployed / 1000.0), 2)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------- pure evaluation (testable, no I/O) ----------------

def evaluate(stats: dict, history: dict, baselines: dict = None) -> dict:
    """Pure. Given per-bot stats for the day + per-bot history of prior
    per1k values, return {flags, recs, changes}. `changes` are bounded tune
    proposals: {bot, name, param, old, new, reason}.
    """
    b = {**BASE, **(baselines or {})}
    flags, recs, changes = [], [], []
    for bot_id, s in stats.items():
        name = s["name"]
        p1k = s["per1k"]
        churn = s["orders"]
        hist = history.get(bot_id, [])
        # rolling z-score vs this bot's own history
        z = 0.0
        if len(hist) >= 3:
            mu = fmean(hist); sd = pstdev(hist)
            if sd > 0:
                z = (p1k - mu) / sd
        impact = abs(s["realized"])

        # ---- anomaly flags (ranked later by impact) ----
        if churn > b["churn_max"] and p1k < 0:
            flags.append({"bot": name, "sev": "high", "impact": impact,
                          "msg": f"over-trading: {churn} orders, {p1k:+.1f}/$1k"})
        if p1k <= b["per1k_floor"]:
            flags.append({"bot": name, "sev": "high", "impact": impact,
                          "msg": f"bleeding: {p1k:+.1f} realized/$1k"})
        if abs(z) >= b["z_flag"] and len(hist) >= 3:
            flags.append({"bot": name, "sev": "med", "impact": impact,
                          "msg": f"per1k anomaly z={z:+.1f} vs its own baseline"})
        if s["failed"] > b["failed_max"]:
            flags.append({"bot": name, "sev": "low", "impact": 0,
                          "msg": f"{s['failed']} failed orders (likely market-hours/options)"})

        if name == CONTROL:
            continue  # control: observe, never tune

        # ---- bounded, risk-reducing auto-tunes ----
        if churn > b["churn_max"] and p1k < 0:
            old = s["cooldown_min"]; new = clamp(old + b["cooldown_step"], old, b["cooldown_cap"])
            if new != old:
                changes.append({"bot": bot_id, "name": name, "param": "cooldown_min",
                                "old": old, "new": new,
                                "reason": f"over-trading ({churn} orders, {p1k:+.1f}/$1k)"})
        if p1k < 0 and s["avg_conf"] and s["avg_conf"] < 0.55:
            old = s["min_confidence"]; new = round(clamp(old + b["conf_step"], old, b["conf_cap"]), 2)
            if new != old:
                changes.append({"bot": bot_id, "name": name, "param": "min_confidence",
                                "old": old, "new": new,
                                "reason": f"losing on low-conviction (avg conf {s['avg_conf']:.2f})"})
        # persistent bleeder -> pause
        recent = hist[-(b["pause_after_days"] - 1):] if b["pause_after_days"] > 1 else []
        if p1k <= b["per1k_floor"] and recent and all(h <= b["per1k_floor"] for h in recent):
            changes.append({"bot": bot_id, "name": name, "param": "enabled",
                            "old": True, "new": False,
                            "reason": f"bled <= {b['per1k_floor']}/$1k for {b['pause_after_days']}+ days"})

        # ---- recommendations (human-oversight, not auto-applied) ----
        if p1k > 5 and churn <= b["churn_max"]:
            recs.append({"bot": name, "impact": impact,
                         "msg": f"performing ({p1k:+.1f}/$1k) -- consider a small size increase (manual)"})

    flags.sort(key=lambda f: f["impact"], reverse=True)
    recs.sort(key=lambda r: r["impact"], reverse=True)
    return {"flags": flags, "recs": recs, "changes": changes}


# ---------------- I/O + orchestration ----------------

def _ledger_rows(bot_id: str) -> list[dict]:
    p = DATA / "bots" / bot_id / "trades.csv"
    if not p.exists():
        return []
    try:
        return list(csv.DictReader(open(p, newline="")))
    except Exception:
        return []


def _realized_today(trading, id2name: dict) -> dict:
    """Per-bot realized P&L for today's date from FIFO-matched fills."""
    from dashboard import perf
    out = {name: 0.0 for name in id2name.values()}
    today = _today()
    name2id = {v: k for k, v in id2name.items()}
    curves = perf.realized_curves(trading, id2name)
    res = {}
    for name, pts in curves.items():
        prev = 0.0; today_real = 0.0; last_before = 0.0
        for pt in pts:
            inc = pt["pnl"] - prev
            if str(pt["t"]).startswith(today):
                today_real += inc
            prev = pt["pnl"]
        res[name2id.get(name, name)] = round(today_real, 2)
    return res


def gather_stats(trading, bots_registry: dict) -> dict:
    id2name = {k: v["name"] for k, v in bots_registry.items()}
    realized = _realized_today(trading, id2name)
    today = _today()
    stats = {}
    for bot_id, b in bots_registry.items():
        rows = _ledger_rows(bot_id)
        orders = deployed = failed = 0
        confs = []
        for r in rows:
            if not str(r.get("ts", "")).startswith(today):
                continue
            a = r.get("action", "")
            if a == "order":
                orders += 1
                try:
                    deployed += float(r.get("notional") or 0)
                except ValueError:
                    pass
                try:
                    confs.append(float(r.get("confidence") or 0))
                except ValueError:
                    pass
            elif a == "order_failed":
                failed += 1
        real = realized.get(bot_id, 0.0)
        stats[bot_id] = {
            "name": b["name"], "orders": orders, "deployed": round(deployed, 2),
            "failed": failed, "realized": real, "per1k": per_1k(real, deployed),
            "avg_conf": round(fmean(confs), 3) if confs else 0.0,
            "cooldown_min": b["params"].get("cooldown_min", 0),
            "min_confidence": b["params"].get("min_confidence", 0.45),
            "status": b.get("status", "?"),
        }
    return stats


def _load_history() -> dict:
    """bot_id -> [per1k, ...] across prior days."""
    hist = {}
    if HISTORY.exists():
        for line in HISTORY.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            for bid, p1k in rec.get("per1k", {}).items():
                hist.setdefault(bid, []).append(p1k)
    return hist


def _append_history(stats: dict) -> None:
    DATA.mkdir(exist_ok=True)
    rec = {"date": _today(), "per1k": {b: s["per1k"] for b, s in stats.items()},
           "realized": {b: s["realized"] for b, s in stats.items()}}
    with open(HISTORY, "a") as f:
        f.write(json.dumps(rec) + "\n")


def build_digest(stats, result, applied, equity, day_pl) -> str:
    d = _today()
    L = [f"# Daily Optimization Digest — {d}", ""]
    L.append(f"**Account equity:** ${equity:,.2f}  |  **Day P&L:** ${day_pl:+,.2f}")
    L.append("")
    L.append("## Per-strategy (Realized P&L per $1k deployed)")
    L.append("")
    L.append("| Bot | Orders | Deployed | Realized | per $1k | avg conf |")
    L.append("|---|---|---|---|---|---|")
    for s in sorted(stats.values(), key=lambda x: x["per1k"]):
        L.append(f"| {s['name']} | {s['orders']} | ${s['deployed']:,.0f} | "
                 f"${s['realized']:+.2f} | {s['per1k']:+.1f} | {s['avg_conf']:.2f} |")
    L.append("")
    L.append("## Flags (ranked by $ impact)")
    if result["flags"]:
        for f in result["flags"]:
            L.append(f"- **[{f['sev'].upper()}]** {f['bot']}: {f['msg']}  (impact ${f['impact']:.0f})")
    else:
        L.append("- none")
    L.append("")
    L.append("## Auto-tunes applied (bounded, risk-reducing)")
    if applied:
        for c in applied:
            L.append(f"- {c['name']}: `{c['param']}` {c['old']} → {c['new']}  — {c['reason']}")
    else:
        L.append("- none")
    L.append("")
    L.append("## Recommendations (manual review)")
    if result["recs"]:
        for r in result["recs"]:
            L.append(f"- {r['bot']}: {r['msg']}")
    else:
        L.append("- none")
    L.append("")
    L.append("_Auto-tunes only ever make the system more conservative. Validation "
             "remains: beat SPY over weeks, net of slippage._")
    return "\n".join(L)


def _log(msg):
    DATA.mkdir(exist_ok=True)
    line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def run_daily(apply: bool = False) -> str:
    from trader import config
    from alpaca.trading.client import TradingClient
    from dashboard import bots as botmgr
    cfg = config.load()
    trading = TradingClient(cfg.alpaca_key, cfg.alpaca_secret, paper=True)
    reg = botmgr._load()
    botmgr.list_bots()  # refresh statuses
    reg = botmgr._load()

    stats = gather_stats(trading, reg)
    history = _load_history()
    result = evaluate(stats, history)

    # account snapshot
    try:
        acct = trading.get_account()
        equity = float(acct.equity); day_pl = float(acct.equity) - float(acct.last_equity)
    except Exception:
        equity = day_pl = 0.0

    applied = []
    if apply:
        for c in result["changes"]:
            bid = c["bot"]
            b = reg.get(bid)
            if not b:
                continue
            if c["param"] == "enabled":
                botmgr.stop_bot(bid)
                b = botmgr._load()[bid]; b["enabled"] = False
                r = botmgr._load(); r[bid]["enabled"] = False; botmgr._save(r)
            else:
                r = botmgr._load(); r[bid]["params"][c["param"]] = c["new"]; botmgr._save(r)
                # restart to apply new param live
                botmgr.stop_bot(bid); time.sleep(1); botmgr.start_bot(bid)
            applied.append(c)
            _log(f"AUTO-TUNE {c['name']}: {c['param']} {c['old']}->{c['new']} ({c['reason']})")

    _append_history(stats)
    digest = build_digest(stats, result, applied, equity, day_pl)
    DIGESTS.mkdir(parents=True, exist_ok=True)
    (DIGESTS / f"{_today()}.md").write_text(digest, encoding="utf-8")
    (DIGESTS / "latest.md").write_text(digest, encoding="utf-8")
    _log(f"daily review done: {len(result['flags'])} flags, {len(applied)} tunes applied")
    return digest


def daemon():
    hour = int(os.getenv("OPT_HOUR", "8"))     # local hour to run
    _log(f"optimizer daemon up: daily run at {hour:02d}:00 local (apply=on)")
    last = None
    while True:
        now = datetime.now()
        if now.hour == hour and last != now.date():
            try:
                run_daily(apply=True)
            except Exception as e:
                _log(f"run error: {e}")
            last = now.date()
        time.sleep(60)


if __name__ == "__main__":
    import sys
    if "--daemon" in sys.argv:
        daemon()
    else:
        print(run_daily(apply="--apply" in sys.argv))
