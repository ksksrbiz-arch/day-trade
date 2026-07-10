"""Free-model cognition, suite 2: deeper reasoning jobs across the platform.

Builds on trader/cognition.py (same helpers, same data dir) with a second set of
gated, auto-safe LLM actions that reason over the platform's OWN outputs:

  macro_analysis()  cross-asset macro thesis from the transformer's driver
                    weights + regime + factor leadership + news -> a directional
                    market belief.
  second_opinion()  when the council is most undecided on a name, write an
                    independent bull-vs-bear second opinion (advisory).
  theory_synthesis() read the system's beliefs + attribution + episodes and
                    synthesize its CURRENT operating theory -- a self-model of how
                    it thinks it makes money right now.
  watchlist_review() review the armed watch->strike list and recommend which
                    theses still hold and which have gone stale.
  strategy_review() memo on the learned confluence weights + voice attribution
                    with concrete, testable adjustments (advisory).
  anomaly_explain() when the mesh anomaly detector fires, explain what the spike/
                    silence/burst likely means and what to watch.

Safe by construction: reads state, writes to memory/analysis + mesh only. No job
places, sizes, or cancels a trade.
"""
from __future__ import annotations

import json
import time

from .cognition import _save, last, _reason_json, _publish  # reuse suite-1 helpers


def _reason(system: str, user: str, max_tokens: int = 360, temp: float = 0.4) -> str:
    try:
        from . import reasoner
        return reasoner.reason(user, system=system, max_tokens=max_tokens, temperature=temp) or ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
def macro_analysis() -> dict:
    out: dict = {"ok": False}
    bits = []
    try:
        from . import tnet
        a = tnet.analyze("SPY")
        dr = (a.get("drivers") or {}).get("weights", {})
        top = sorted(dr.items(), key=lambda kv: kv[1], reverse=True)[:3]
        bits.append("Transformer drivers: " + ", ".join(f"{k} {v:.0%}" for k, v in top if v))
        bits.append(f"SPY vol_z {a.get('vol_z')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import market_brain
        bits.append("Regime: " + market_brain.cached_regime("neutral"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import factors
        rk = factors.ranking()[:3]
        if rk:
            bits.append("Factor leaders: " + ", ".join(
                f"{r.get('symbol','')}({r.get('score', r.get('z',0)):+.2f})" for r in rk))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import newshub
        ms = (newshub.aggregate(limit=40) or {}).get("market_sentiment", {})
        bits.append(f"News sentiment {ms.get('net')} ({ms.get('label')})")
    except Exception:  # noqa: BLE001
        pass
    ctx = "; ".join(b for b in bits if b)
    if not ctx:
        out["error"] = "no macro context"
        return out
    system = ("You are a macro strategist. From these cross-asset signals, state the "
              "dominant driver, the implied risk-on/off tilt, and a directional bias for "
              "US equities with a confidence. Be specific, 90 words max.")
    text = _reason(system, f"Signals: {ctx}\n\nWrite the macro read.", max_tokens=300)
    if not text:
        out["error"] = "model unavailable"
        return out
    # extract a coarse directional belief
    low = text.lower()
    direction = 1 if ("risk-on" in low or "bullish" in low) else (-1 if ("risk-off" in low or "bearish" in low) else 0)
    try:
        from . import beliefs
        beliefs.upsert(f"macro read: {text[:120]}", target="market",
                       direction=direction, regime="any", confidence=0.55)
    except Exception:  # noqa: BLE001
        pass
    out = {"ok": True, "thesis": text.strip(), "direction": direction, "context": ctx,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("macro", out)
    _publish("brain", "macro", text.strip(), salience=0.7)
    return out


def _borderline_council(k: int = 12):
    """Find the council's most undecided recent call (score nearest 0)."""
    try:
        from . import mesh
        import re
        rows = mesh.recent(k, layers=["council"])
        best = None
        for r in rows:
            m = re.search(r"score ([+-]?\d+\.\d+)", r.get("text", ""))
            sym = r.get("symbol") or ""
            if not m or not sym:
                continue
            score = abs(float(m.group(1)))
            if best is None or score < best[1]:
                best = (sym, score, r.get("text", ""))
        return best
    except Exception:  # noqa: BLE001
        return None


def second_opinion() -> dict:
    out: dict = {"ok": False}
    b = _borderline_council()
    if not b:
        out["error"] = "no council calls to weigh"
        return out
    sym, score, line = b
    ctx = [f"Council line: {line}"]
    try:
        from . import newshub
        cats = newshub.catalysts(sym, k=4)
        if cats:
            ctx.append("Recent news: " + "; ".join(c.get("title", "")[:80] for c in cats[:3]))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import factors
        fs = factors.score_signal(sym)
        if fs is not None:
            ctx.append(f"Factor score {fs:+.2f}")
    except Exception:  # noqa: BLE001
        pass
    system = ("You are an independent second opinion on a trading desk. The council is "
              "nearly split on this name. Give the strongest BULL case and BEAR case in one "
              "line each, then your call (buy/hold/avoid) with a reason. 80 words max.")
    text = _reason(system, f"Name: {sym}\n" + "\n".join(ctx) + "\n\nYour second opinion.",
                   max_tokens=280)
    if not text:
        out["error"] = "model unavailable"
        return out
    out = {"ok": True, "symbol": sym, "opinion": text.strip(),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("second_opinion", out)
    _publish("desk", "second_opinion", f"{sym}: {text.strip()[:140]}", salience=0.65, symbol=sym)
    return out


def theory_synthesis() -> dict:
    out: dict = {"ok": False}
    bits = []
    try:
        from . import beliefs
        bs = beliefs.all_beliefs()[:10]
        if bs:
            bits.append("Beliefs: " + "; ".join(
                f"{b.get('claim','')[:70]} (u{b.get('utility',0):+.2f})" for b in bs[:8]))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import attribution
        rep = attribution.report()
        vs = rep.get("voices", [])[:5]
        if vs:
            bits.append("Attribution: " + ", ".join(
                f"{v.get('voice','')} {v.get('mean_attr', v.get('attr',0)):+.3f}" for v in vs))
        elif rep.get("summary"):
            bits.append(rep["summary"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import episodes
        st = episodes.stats()
        bits.append(f"Episodes: {st.get('total',0)} logged, {st.get('resolved',0)} resolved")
    except Exception:  # noqa: BLE001
        pass
    ctx = "\n".join(b for b in bits if b)
    if not ctx:
        out["error"] = "no self-state"
        return out
    system = ("You are the reflective core of a trading system. From your own beliefs, "
              "voice attribution and experience, state your CURRENT operating theory: how "
              "you believe you make money right now, what you trust, and your biggest "
              "uncertainty. First person, 100 words max.")
    text = _reason(system, f"Your internal state:\n{ctx}\n\nState your operating theory.",
                   max_tokens=340)
    if not text:
        out["error"] = "model unavailable"
        return out
    out = {"ok": True, "theory": text.strip(),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("theory", out)
    _publish("reasoning", "theory", text.strip(), salience=0.7)
    try:
        from . import ltm
        if ltm.available():
            ltm.remember("Current operating theory", text.strip(),
                         dedup_key=f"theory::{int(time.time()//86400)}", topics=["theory", "self"])
    except Exception:  # noqa: BLE001
        pass
    return out


def watchlist_review() -> dict:
    out: dict = {"ok": False, "reviews": []}
    try:
        from . import watchlist
        wl = watchlist.WatchList()
        wl.prune()  # drop mechanically-expired first
        active = wl.active()
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
        return out
    if not active:
        out.update({"ok": True, "note": "watchlist empty"})
        return out
    lines = [f"{e.get('symbol','')} {e.get('thesis','')} trig {e.get('trigger','')} "
             f"conf {e.get('confidence','')} src {e.get('source','')}" for e in active[:12]]
    system = ("You review a trading watch->strike list. For each armed thesis say KEEP or "
              "STALE with a one-line reason. Be strict about stale theses.")
    user = ("Armed theses:\n" + "\n".join(lines) + '\n\nReturn ONLY JSON: '
            '{"reviews":[{"symbol":"...","verdict":"keep|stale","reason":"..."}]}')
    data = _reason_json(system, user, max_tokens=500)
    if not data:
        out["error"] = "model unavailable"
        return out
    reviews = data.get("reviews", []) if isinstance(data, dict) else []
    out = {"ok": True, "reviews": reviews[:12], "active": len(active),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("watchlist_review", out)
    stale = [r.get("symbol") for r in reviews if r.get("verdict") == "stale"]
    _publish("desk", "watchlist_review",
             f"reviewed {len(active)} armed theses; {len(stale)} look stale", salience=0.55)
    return out


def strategy_review() -> dict:
    out: dict = {"ok": False}
    bits = []
    try:
        from . import backprop
        bc = backprop.card()
        if bc.get("trained"):
            emp = bc.get("emphasis", {}) or {}
            top = sorted(emp.items(), key=lambda kv: kv[1], reverse=True)[:5]
            bits.append("Learned emphasis: " + ", ".join(f"{k} {v:.0%}" for k, v in top))
        else:
            bits.append("Confluence weights: not yet trained")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import attribution
        rep = attribution.report()
        vs = rep.get("voices", [])[:6]
        if vs:
            bits.append("Voice attribution (mean/decision): " + ", ".join(
                f"{v.get('voice','')} {v.get('mean_attr', v.get('attr',0)):+.3f}" for v in vs))
        elif rep.get("summary"):
            bits.append(rep["summary"])
    except Exception:  # noqa: BLE001
        pass
    ctx = "\n".join(b for b in bits if b)
    if not ctx:
        out["error"] = "no strategy state"
        return out
    system = ("You are a quant reviewing a signal-blend's learned weights and each voice's "
              "realized attribution. Recommend 2-3 concrete, testable adjustments (which "
              "voice to lean on or trim, and why). Advisory only. 90 words max.")
    text = _reason(system, f"State:\n{ctx}\n\nWrite the review.", max_tokens=320)
    if not text:
        out["error"] = "model unavailable"
        return out
    out = {"ok": True, "review": text.strip(), "context": ctx,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("strategy_review", out)
    _publish("ml", "strategy_review", text.strip(), salience=0.6)
    return out


def anomaly_explain() -> dict:
    out: dict = {"ok": False, "explanations": []}
    try:
        from . import mesh_anomaly
        summ = mesh_anomaly.summary()
        anoms = summ.get("anomalies", [])
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
        return out
    if not anoms:
        out.update({"ok": True, "note": "no anomalies"})
        return out
    lines = [json.dumps(a)[:180] for a in anoms[:6]]
    system = ("You interpret anomalies in a trading system's internal activity mesh "
              "(spikes, silences, bursts by layer). For each, say what it likely means and "
              "whether it warrants attention. Brief.")
    user = ("Anomalies:\n" + "\n".join(lines) + '\n\nReturn ONLY JSON: '
            '{"explanations":[{"what":"...","meaning":"...","action":"watch|ignore|investigate"}]}')
    data = _reason_json(system, user, max_tokens=500)
    if not data:
        out["error"] = "model unavailable"
        return out
    exps = data.get("explanations", []) if isinstance(data, dict) else []
    out = {"ok": True, "explanations": exps[:6], "n_anomalies": len(anoms),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save("anomaly", out)
    if exps:
        _publish("mesh", "anomaly_explain",
                 f"{len(anoms)} anomalies: {str(exps[0].get('meaning',''))[:110]}", salience=0.6)
    return out


if __name__ == "__main__":
    import sys
    fn = sys.argv[1] if len(sys.argv) > 1 else "macro_analysis"
    print(json.dumps(globals()[fn](), indent=2))
