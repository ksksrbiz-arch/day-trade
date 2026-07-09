"""Performance attribution -- which VOICE actually makes money.

The backprop decision log already records the per-voice score vector at every
decision. This module resolves each decision forward and attributes the realized
directional return to each voice by its *share of the composite* it helped form:

    composite = Σ_k  w_k · score_k
    share_k   = (w_k · score_k) / composite          (Σ share_k = 1)
    r_dir     = sign(composite) · (p1/p0 − 1)         (realized, in the traded dir)
    attribution_k += share_k · r_dir                  (Σ_k attribution_k = r_dir)

So the per-voice attributions sum exactly to the realized directional return:
the total return is *decomposed* across the voices that produced it. A voice that
keeps agreeing with trades that work accumulates positive attribution; one that
leans the system the wrong way accumulates negative. This turns the learned
weights from "what predicts direction" into "what is actually profitable."

Honest: a decision only counts once it has matured (forward window elapsed);
thin voices are marked "maturing".

  report(...)        -> structured dict (also served at /api/attribution)
  format_report(...) -> markdown
"""
from __future__ import annotations

import json
import os
import time

MIN_DECISIONS = 20      # resolved decisions a voice needs before a verdict


def _weights() -> dict:
    """Effective confluence weights: backprop-learned emphasis if trained,
    else normalized static base over the method set."""
    from . import backprop
    le = None
    try:
        le = backprop.learned_emphasis()
    except Exception:  # noqa: BLE001
        le = None
    if le:
        return {m: float(le.get(m, 0.0)) for m in backprop.METHODS}
    from . import alpha
    base = {m: alpha._BASE_W.get(m, 0.12) for m in backprop.METHODS}
    s = sum(base.values()) or 1.0
    return {m: base[m] / s for m in backprop.METHODS}


_REP_CACHE: dict = {"key": None, "at": 0.0, "val": None}
_REP_TTL = 600.0


def report(min_decisions: int = MIN_DECISIONS) -> dict:
    from . import backprop
    import time as _t
    # memoized on the decision-log signature (same rationale as build_dataset):
    # this re-resolves ~1.5k rows and is called by /api/voices + prune/promote.
    try:
        st = os.stat(backprop.DECISIONS)
        _key = (st.st_mtime_ns, st.st_size, min_decisions)
    except OSError:
        _key = None
    if (_key is not None and _REP_CACHE["key"] == _key
            and _REP_CACHE["val"] is not None and (_t.time() - _REP_CACHE["at"]) < _REP_TTL):
        return _REP_CACHE["val"]
    methods = backprop.METHODS
    blank = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "resolved": 0, "total_dir_return_pct": 0.0, "voices": [],
             "weights_source": None,
             "summary": "No resolved decisions yet — attribution will populate as "
                        "logged decisions mature in forward time."}
    if not os.path.exists(backprop.DECISIONS):
        return blank

    w = _weights()
    le_trained = False
    try:
        le_trained = bool(backprop.learned_emphasis())
    except Exception:  # noqa: BLE001
        pass

    per = {m: {"opinions": 0, "lead": 0, "lead_hits": 0, "attr": 0.0,
               "agree_ret": 0.0, "agree_n": 0} for m in methods}
    resolved = 0
    total_dir = 0.0

    for ln in open(backprop.DECISIONS, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        res = backprop._price_fn(r["symbol"], r.get("asset", "equity"),
                                 r["day"], r.get("horizon", 5))
        if not res:
            continue
        p0, p1 = res
        if not p0:
            continue
        scores = r.get("scores", {})
        contrib = {m: w.get(m, 0.0) * float(scores.get(m, 0.0) or 0.0) for m in methods}
        composite = sum(contrib.values())
        if composite == 0:
            continue
        sign = 1.0 if composite > 0 else -1.0
        r_dir = sign * (p1 / p0 - 1.0)
        resolved += 1
        total_dir += r_dir
        lead_m, lead_share = None, -1e9
        for m in methods:
            sc = float(scores.get(m, 0.0) or 0.0)
            if abs(sc) >= 0.1:
                per[m]["opinions"] += 1
            share = contrib[m] / composite          # Σ shares = 1
            per[m]["attr"] += share * r_dir
            if sc != 0:
                if (sc > 0) == (sign > 0):
                    per[m]["agree_ret"] += r_dir
                    per[m]["agree_n"] += 1
            if share > lead_share:
                lead_share, lead_m = share, m
        if lead_m is not None:
            per[lead_m]["lead"] += 1
            if r_dir > 0:
                per[lead_m]["lead_hits"] += 1

    if resolved == 0:
        _REP_CACHE.update(key=_key, at=_t.time(), val=blank)
        return blank

    voices = []
    for m in methods:
        d = per[m]
        voices.append({
            "voice": m,
            "opinions": d["opinions"],
            "attributed_return_pct": round(d["attr"] / resolved * 100, 3),   # MEAN per decision
            "lead_decisions": d["lead"],
            "lead_hit_rate": round(d["lead_hits"] / d["lead"], 3) if d["lead"] else None,
            "agree_decisions": d["agree_n"],
            "avg_return_when_agree_pct": round(d["agree_ret"] / d["agree_n"] * 100, 3) if d["agree_n"] else None,
            "weight": round(w.get(m, 0.0), 3),
            "verdict": ("maturing (%d/%d)" % (d["agree_n"], min_decisions)) if d["agree_n"] < min_decisions
            else ("profitable" if d["attr"] > 0 else "unprofitable"),
        })
    voices.sort(key=lambda v: v["attributed_return_pct"], reverse=True)

    mature = [v for v in voices if not v["verdict"].startswith("maturing")]
    if mature:
        best = mature[0]
        worst = mature[-1]
        summary = (f"Most profitable voice: {best['voice']} "
                   f"({best['attributed_return_pct']:+.2f}% attributed). "
                   f"Least: {worst['voice']} ({worst['attributed_return_pct']:+.2f}%). "
                   f"Avg directional return per decision: {total_dir / resolved * 100:+.3f}% "
                   f"over {resolved} decisions.")
    else:
        summary = (f"{resolved} decisions resolved; no voice has {min_decisions} agreeing "
                   f"decisions yet — attribution still maturing. Total directional "
                   f"return per decision: {total_dir / resolved * 100:+.3f}%.")

    _out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "resolved": resolved, "total_dir_return_pct": round(total_dir / resolved * 100, 3),
            "weights_source": "backprop-learned" if le_trained else "static base",
            "voices": voices, "summary": summary}
    _REP_CACHE.update(key=_key, at=_t.time(), val=_out)
    return _out


def format_report(rep: dict | None = None) -> str:
    rep = rep or report()
    L = ["# Voice Attribution — who actually makes money", "",
         f"_Generated {rep['generated']} · {rep['resolved']} resolved decisions · "
         f"weights: {rep['weights_source'] or 'n/a'} · verdict needs {MIN_DECISIONS} agreeing_", "",
         rep["summary"], "",
         "| Voice | Weight | Opinions | Attributed P&L | Lead trades | Lead hit | Avg ret when agree | Verdict |",
         "|---|--:|--:|--:|--:|--:|--:|---|"]
    for v in rep["voices"]:
        lh = f"{v['lead_hit_rate']:.0%}" if v.get("lead_hit_rate") is not None else "—"
        ar = f"{v['avg_return_when_agree_pct']:+.2f}%" if v.get("avg_return_when_agree_pct") is not None else "—"
        L.append(f"| {v['voice']} | {v['weight']:.2f} | {v['opinions']} | "
                 f"{v['attributed_return_pct']:+.2f}% | {v['lead_decisions']} | {lh} | {ar} | {v['verdict']} |")
    L += ["", "_Attributions sum to the total realized directional return — this is a "
          "decomposition of actual P&L across voices, not a backtest._"]
    return "\n".join(L)


def write_and_publish() -> dict:
    rep = report()
    md = format_report(rep)
    path = os.path.join("data", "reports", "attribution.md")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import mesh
        mesh.publish("attribution", "voices", rep["summary"], salience=0.65)
    except Exception:  # noqa: BLE001
        pass
    return {"path": path, "summary": rep["summary"], "resolved": rep["resolved"]}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print(format_report())
    print("\n", write_and_publish())
