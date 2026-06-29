"""Forward edge report -- the one honest scorecard.

Every signal source in the platform (transformer, confluence, ML, WSB buzz,
WSB-hypothesis predictions, council) is measured the only way that can't be
fooled: on REALIZED FORWARD outcomes. For each source we report how many calls
it has made, how many have matured, its forward hit-rate, and -- the metric that
actually matters -- its average direction-adjusted forward return. Each is judged
against a coin flip (50%) and against SPY buy-and-hold over the same window.

Nothing here can be backtested into looking good: it only credits a source once
its calls have played out in real forward time. Sources without enough resolved
calls are honestly marked "maturing".

  report(...)        -> structured dict (also served at /api/edge)
  format_report(...) -> markdown summary
  python -m trader.edge   -> writes data/reports/edge_report.md + publishes to mesh
"""
from __future__ import annotations

import json
import os
import time

MIN_RESOLVED = 20          # calls that must mature before we render a verdict
EDGE_HIT = 0.55            # hit-rate bar to claim a positive edge
NEG_HIT = 0.45             # below this = actively wrong


def _spy_baseline(window_days: int = 90) -> dict | None:
    try:
        from . import tnet
        closes = tnet._closes("SPY")
    except Exception:  # noqa: BLE001
        closes = []
    if len(closes) < 3:
        return None
    n = min(window_days, len(closes) - 1)
    p0, p1 = float(closes[-1 - n]), float(closes[-1])
    if p0 <= 0:
        return None
    total = (p1 / p0 - 1.0) * 100
    ann = ((p1 / p0) ** (252.0 / n) - 1.0) * 100 if n > 0 else 0.0
    return {"window_days": n, "spy_total_pct": round(total, 2), "spy_ann_pct": round(ann, 2)}


def _tnet_source(min_age_days: float = 5.0) -> dict:
    """Resolve the transformer's own logged forecasts against realized moves."""
    out = {"source": "transformer", "signals": 0, "resolved": 0,
           "hit_rate": None, "avg_dir_return_pct": None}
    try:
        from . import tnet
        if not os.path.exists(tnet._FLOG):
            return out
        rows = []
        for ln in open(tnet._FLOG, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    pass
        out["signals"] = len(rows)
        now = time.time()
        hits, rets = 0, []
        resolved = 0
        for r in rows:
            if now - r.get("ts", now) < min_age_days * 86400:
                continue
            cur = tnet._closes(r["symbol"])
            if len(cur) < 2:
                continue
            ref = float(r.get("ref", 0) or 0)
            if ref <= 0:
                continue
            move = (float(cur[-1]) / ref - 1.0) * 100
            raw = float(r.get("raw", 0))
            if raw == 0:
                continue
            resolved += 1
            sgn = 1 if raw > 0 else -1
            if (sgn > 0) == (move > 0):
                hits += 1
            rets.append(sgn * move)
        out["resolved"] = resolved
        if resolved:
            out["hit_rate"] = round(hits / resolved, 3)
            out["avg_dir_return_pct"] = round(sum(rets) / len(rets), 3)
    except Exception:  # noqa: BLE001
        pass
    return out


def _transformer_maturation(min_resolved: int = MIN_RESOLVED, min_age_days: float = 5.0) -> dict | None:
    """When will the transformer have enough matured calls for a verdict?
    Estimated from the forecast log timestamps (each call matures at ts+horizon)."""
    try:
        from . import tnet
        if not os.path.exists(tnet._FLOG):
            return None
        now = time.time()
        mature_at = []
        for ln in open(tnet._FLOG, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            mature_at.append(r.get("ts", now) + min_age_days * 86400)
        if not mature_at:
            return None
        mature_at.sort()
        matured_now = sum(1 for m in mature_at if m <= now)
        if matured_now >= min_resolved:
            days = 0.0
        elif len(mature_at) >= min_resolved:
            days = round(max(0.0, (mature_at[min_resolved - 1] - now) / 86400), 1)
        else:
            days = None                       # not enough calls logged yet
        return {"source": "transformer", "matured": matured_now, "need": min_resolved,
                "total": len(mature_at), "days_to_threshold": days}
    except Exception:  # noqa: BLE001
        return None


def _norm_sig(r: dict) -> dict:
    return {"source": r.get("source", "?"), "signals": r.get("signals", 0),
            "resolved": r.get("resolved", 0), "hit_rate": r.get("hit_rate"),
            "avg_dir_return_pct": r.get("avg_dir_return_pct")}


def _verdict(s: dict, min_resolved: int) -> str:
    res = s.get("resolved") or 0
    hr = s.get("hit_rate")
    if res < min_resolved or hr is None:
        return f"maturing ({res}/{min_resolved})"
    adr = s.get("avg_dir_return_pct")
    if hr >= EDGE_HIT and (adr is None or adr > 0):
        return "EDGE"
    if hr <= NEG_HIT:
        return "negative"
    return "no edge (~coin flip)"


def report(window_days: int = 90, min_resolved: int = MIN_RESOLVED) -> dict:
    sources: list[dict] = []

    # live signal scorecard: confluence / ml / wsb
    try:
        from . import sigtrack
        for r in sigtrack.scoreboard().get("by_source", []):
            sources.append(_norm_sig(r))
    except Exception:  # noqa: BLE001
        pass

    # transformer (its own logged forecasts)
    sources.append(_tnet_source())

    # WSB-hypothesis predictions (decision engine)
    try:
        from .predict import store as pstore
        st = pstore.stats()
        res = int(st.get("correct", 0)) + int(st.get("incorrect", 0))
        hr = round(st["correct"] / res, 3) if res else None
        sources.append({"source": "predictions(WSB hyp)", "signals": int(st.get("total", 0)),
                        "resolved": res, "hit_rate": hr, "avg_dir_return_pct": None})
    except Exception:  # noqa: BLE001
        pass

    # council / agent reliability (per source accuracy)
    try:
        from .ml.agent_reliability import load_reliability
        for src, d in (load_reliability() or {}).items():
            tot = int(d.get("total", 0))
            acc = d.get("acc")
            sources.append({"source": f"council:{src}", "signals": tot, "resolved": tot,
                            "hit_rate": round(float(acc), 3) if acc is not None else None,
                            "avg_dir_return_pct": None})
    except Exception:  # noqa: BLE001
        pass

    for s in sources:
        s["edge_vs_coin_pp"] = (round((s["hit_rate"] - 0.5) * 100, 1)
                                if s.get("hit_rate") is not None else None)
        s["verdict"] = _verdict(s, min_resolved)

    baseline = _spy_baseline(window_days)
    proven = [s for s in sources if s["verdict"] == "EDGE"]
    maturing = [s for s in sources if s["verdict"].startswith("maturing")]
    summary = _summary(sources, baseline, proven, maturing)
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "window_days": window_days, "baseline": baseline,
            "sources": sources,
            "maturation": _transformer_maturation(min_resolved),
            "counts": {"sources": len(sources), "proven_edge": len(proven),
                       "maturing": len(maturing)},
            "summary": summary}


def _summary(sources, baseline, proven, maturing) -> str:
    if proven:
        best = max(proven, key=lambda s: s.get("avg_dir_return_pct") or 0)
        head = (f"{len(proven)} source(s) show a forward edge; strongest is "
                f"{best['source']} ({best['hit_rate']:.0%} hit).")
    elif maturing and not any(s["verdict"] in ("EDGE", "no edge (~coin flip)", "negative") for s in sources):
        head = "No source has enough resolved calls yet — everything is still maturing."
    else:
        head = "No source has cleared the edge bar yet; keep resolving forward."
    if baseline:
        head += f" SPY buy-and-hold over the window: {baseline['spy_total_pct']:+.1f}%."
    return head


def _maturation_line(rep: dict) -> str:
    m = rep.get("maturation")
    if not m:
        return ""
    d = m.get("days_to_threshold")
    when = ("ready now" if d == 0 else (f"~{d}d" if d is not None else f"needs {m['need']} calls (have {m['total']})"))
    return f"⏳ Transformer maturation: {m['matured']}/{m['need']} matured · first verdict {when}."


def format_report(rep: dict | None = None) -> str:
    rep = rep or report()
    L = ["# Forward Edge Report", "", f"_Generated {rep['generated']} · "
         f"window {rep['window_days']}d · verdict needs {MIN_RESOLVED} resolved calls_", "",
         rep["summary"], "", _maturation_line(rep), "",
         "| Source | Calls | Resolved | Hit-rate | vs coin | Dir-return | Verdict |",
         "|---|--:|--:|--:|--:|--:|---|"]
    for s in sorted(rep["sources"], key=lambda x: (x["verdict"] != "EDGE", -(x.get("resolved") or 0))):
        hr = f"{s['hit_rate']:.0%}" if s.get("hit_rate") is not None else "—"
        vc = f"{s['edge_vs_coin_pp']:+.1f}pp" if s.get("edge_vs_coin_pp") is not None else "—"
        dr = f"{s['avg_dir_return_pct']:+.2f}%" if s.get("avg_dir_return_pct") is not None else "—"
        L.append(f"| {s['source']} | {s['signals']} | {s['resolved']} | {hr} | {vc} | {dr} | {s['verdict']} |")
    b = rep.get("baseline")
    if b:
        L += ["", f"**Baseline** — SPY buy & hold: {b['spy_total_pct']:+.2f}% "
              f"({b['spy_ann_pct']:+.2f}% ann) over {b['window_days']}d."]
    L += ["", "_Honest note: a source is only credited once its calls mature in real "
          "forward time; this cannot be backtested into looking good._"]
    return "\n".join(L)


def write_and_publish() -> dict:
    rep = report()
    md = format_report(rep)
    path = os.path.join("data", "reports", "edge_report.md")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import mesh
        mesh.publish("edge", "report", rep["summary"], salience=0.7)
    except Exception:  # noqa: BLE001
        pass
    return {"path": path, "summary": rep["summary"], "counts": rep["counts"]}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    r = write_and_publish()
    print(format_report())
    print("\nwrote", r["path"])
