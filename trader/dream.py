"""The dream cycle -- what the system does while the market sleeps.

When the exchange is closed there are no ticks to react to, but there IS a day's
worth of unprocessed experience sitting in memory. Biological sleep is when
brains *consolidate*: replay the day, strengthen what mattered, forget noise,
and integrate new knowledge. This module is that phase for the platform.

It is honest about what it is -- not fantasy, not consciousness. Every phase
operates on real stored data or real historical prices:

  1. REPLAY       resolve matured episodic decisions against realized price;
                  surface behavioural patterns from lived experience.
  2. CONSOLIDATE  reflect (form/revise beliefs from outcomes) and then FORGET:
                  prune faded, unreinforced beliefs so memory stays sharp.
  3. DREAM        counterfactual replay -- walk real past windows, recompute the
                  voices point-in-time, and measure which voice actually predicted
                  the forward move. Recombining real experience into testable
                  beliefs ("trust ml more in calm", "distrust ta in high-vol").
  4. STUDY        curiosity: take the least-evidenced beliefs + the current regime
                  and look them up on the open web, filing what it finds into
                  long-term memory. Learning while idle.
  5. TRAIN        retrain the fusion layers (confluence weights, neural core,
                  transformer calibration) on the now-larger resolved set --
                  champion/challenger gated, paper-only.
  6. JOURNAL      write a first-person dream journal to the mesh + disk so the
                  brain page can show what happened overnight.

Safe by construction: touches memory + training stores only, never the broker.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.path.abspath(os.path.join(_HERE, "..", "data", "dream"))
_LAST = os.path.join(_DIR, "last.json")
_JOURNAL = os.path.join(_DIR, "journal.jsonl")

# keep the counterfactual walk cheap on the free tier
_DREAM_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]
_DREAM_HORIZON = 10
_DREAM_STEP = 5
_DREAM_WARMUP = 70
_MIN_SAMPLES = 12


def _corr(xs, ys) -> float:
    n = len(xs)
    if n < 4:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return max(-1.0, min(1.0, num / (dx * dy)))


# --------------------------------------------------------------------------- #
def _phase_replay() -> dict:
    out: dict = {"name": "replay"}
    try:
        from . import episodes
        out["resolved"] = episodes.resolve()
        out["stats"] = episodes.stats()
        beh = episodes.behavior_stats()
        # keep the sharpest few behavioural patterns
        out["patterns"] = beh[:4] if isinstance(beh, list) else beh
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:140]
    return out


def _phase_consolidate() -> dict:
    out: dict = {"name": "consolidate"}
    try:
        from . import psyche
        out["reflect"] = psyche.reflect()
    except Exception as e:  # noqa: BLE001
        out["reflect_error"] = str(e)[:140]
    try:
        from . import beliefs
        out["forget"] = beliefs.prune()
        out["belief_stats"] = beliefs.stats()
    except Exception as e:  # noqa: BLE001
        out["belief_error"] = str(e)[:140]
    return out


def _phase_dream() -> dict:
    """Counterfactual replay: which voice actually predicted the forward move,
    across real historical windows? Forms voice-trust beliefs from the evidence."""
    out: dict = {"name": "dream", "insights": []}
    try:
        from . import pretrain
        from .ml.dataset import _alpaca_series
    except Exception as e:  # noqa: BLE001
        out["error"] = f"imports: {str(e)[:120]}"
        return out

    # regime label for the beliefs we may form
    regime = "any"
    try:
        from . import market_brain
        regime = market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        pass

    # collect (voice_score, forward_return) samples per voice, point-in-time
    samples: dict[str, list[tuple[float, float]]] = {}
    scanned = 0
    for sym in _DREAM_SYMBOLS:
        try:
            ser = _alpaca_series(sym)
        except Exception:  # noqa: BLE001
            ser = []
        if len(ser) < _DREAM_WARMUP + _DREAM_HORIZON + 5:
            continue
        closes = [float(c) for _, c in ser]
        for idx in range(_DREAM_WARMUP, len(closes) - _DREAM_HORIZON, _DREAM_STEP):
            win = closes[: idx + 1]
            fwd = closes[idx + _DREAM_HORIZON] / closes[idx] - 1.0
            try:
                voices = pretrain._voices(win)
            except Exception:  # noqa: BLE001
                continue
            for v, s in voices.items():
                samples.setdefault(v, []).append((float(s), float(fwd)))
            scanned += 1

    out["scanned_windows"] = scanned
    if scanned < _MIN_SAMPLES:
        out["note"] = "not enough history to dream on yet"
        return out

    # correlate each voice's score with the realized forward move
    formed = 0
    edges = []
    for v, pairs in samples.items():
        if len(pairs) < _MIN_SAMPLES:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        r = _corr(xs, ys)
        edges.append({"voice": v, "corr": round(r, 3), "n": len(pairs)})
    edges.sort(key=lambda e: abs(e["corr"]), reverse=True)
    out["voice_edges"] = edges

    # form beliefs only where the relationship is meaningful
    try:
        from . import beliefs
        for e in edges:
            v, r = e["voice"], e["corr"]
            if abs(r) < 0.08:
                continue
            direction = 1 if r > 0 else -1
            verb = "leads" if r > 0 else "fades against"
            claim = f"in counterfactual replay, the {v} voice {verb} the {_DREAM_HORIZON}d move (r={r:+.2f})"
            conf = round(min(0.8, 0.45 + abs(r)), 3)
            beliefs.upsert(claim, target=v, direction=direction, regime=regime, confidence=conf)
            formed += 1
            out["insights"].append(claim)
    except Exception as e:  # noqa: BLE001
        out["belief_error"] = str(e)[:140]
    out["beliefs_formed"] = formed
    return out


def _phase_study(max_queries: int = 2) -> dict:
    """Curiosity: research the least-evidenced beliefs + the current regime on the
    open web, filing summaries into long-term memory."""
    out: dict = {"name": "study", "queries": []}
    try:
        from . import websearch
    except Exception as e:  # noqa: BLE001
        out["error"] = f"websearch unavailable: {str(e)[:100]}"
        return out

    queries: list[str] = []
    # 1) current macro regime context
    try:
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
        queries.append(f"stock market outlook {reg} regime this week")
    except Exception:  # noqa: BLE001
        queries.append("stock market outlook this week")
    # 2) the belief with the least evidence (what it's most unsure about)
    try:
        from . import beliefs
        bs = beliefs.all_beliefs()
        if bs:
            weak = sorted(bs, key=lambda b: (b.get("evidence_count", 1),
                                             b.get("eff_confidence", 0.0)))[0]
            topic = weak.get("target", "market")
            if topic in ("market", "self"):
                queries.append("what drives stock market volatility regimes")
            else:
                queries.append(f"{topic} technical indicator predictive power stock returns")
    except Exception:  # noqa: BLE001
        pass

    filed = 0
    for q in queries[:max_queries]:
        rec: dict = {"q": q}
        try:
            results = websearch.search(q, k=4) or []
            rec["hits"] = len(results)
            if results:
                summary = "\n".join(
                    f"- {r.get('title','')}: {r.get('snippet','')[:180]}" for r in results[:4]
                )
                rec["top"] = results[0].get("title", "")
                try:
                    from . import ltm
                    if ltm.available():
                        ltm.remember(
                            description=f"Overnight study: {q}",
                            summary_md=summary,
                            dedup_key=f"dream_study::{q}",
                            topics=["dream", "study", "web"],
                        )
                        filed += 1
                        rec["filed"] = True
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:100]
        out["queries"].append(rec)
    out["filed_to_ltm"] = filed
    return out


def _phase_train() -> dict:
    out: dict = {"name": "train", "trained": {}}
    for label, fn in (("confluence", "backprop"), ("cortex", "cortex"), ("tnet_calibration", "tnet")):
        try:
            mod = __import__(f"trader.{fn}", fromlist=["x"])
            if fn == "tnet":
                res = mod.calibrate()
            else:
                res = mod.train()
            ok = bool(res.get("ok", res.get("trained", False))) if isinstance(res, dict) else False
            out["trained"][label] = {"ok": ok, **({k: res[k] for k in list(res)[:4]} if isinstance(res, dict) else {})}
        except Exception as e:  # noqa: BLE001
            out["trained"][label] = {"ok": False, "error": str(e)[:120]}
    return out


def _narrate(phases: dict) -> str:
    """A short first-person journal line from what the night's work produced."""
    bits = []
    rp = phases.get("replay", {})
    res = (rp.get("resolved") or {}).get("resolved", 0)
    if res:
        bits.append(f"replayed and resolved {res} decisions")
    cons = phases.get("consolidate", {})
    ref = cons.get("reflect", {})
    if isinstance(ref, dict) and ref.get("beliefs_formed"):
        bits.append(f"formed {ref['beliefs_formed']} beliefs from outcomes")
    fg = cons.get("forget", {})
    if isinstance(fg, dict) and fg.get("dropped"):
        bits.append(f"let go of {fg['dropped']} faded ones")
    dr = phases.get("dream", {})
    if dr.get("beliefs_formed"):
        top = dr.get("insights", [])
        lead = (top[0].split(", ", 1)[-1] if top else "").strip()
        bits.append(f"dreamed over {dr.get('scanned_windows',0)} past windows and learned that {lead}")
    st = phases.get("study", {})
    if st.get("filed_to_ltm"):
        bits.append(f"studied {st['filed_to_ltm']} topics on the web")
    tr = phases.get("train", {})
    trained_ok = [k for k, v in (tr.get("trained", {}) or {}).items() if v.get("ok")]
    if trained_ok:
        bits.append("retrained " + ", ".join(trained_ok))
    if not bits:
        return "A quiet night -- nothing new had matured yet, so I rested and kept memory tidy."
    return "While the market slept, I " + "; ".join(bits) + "."


def run(phases: list[str] | None = None, reason: str = "market closed") -> dict:
    t0 = time.time()
    want = set(phases or ["replay", "consolidate", "dream", "study", "train"])
    result: dict = {"ok": True, "reason": reason,
                    "session": _session(), "phases": {}}
    if "replay" in want:
        result["phases"]["replay"] = _phase_replay()
    if "consolidate" in want:
        result["phases"]["consolidate"] = _phase_consolidate()
    if "dream" in want:
        result["phases"]["dream"] = _phase_dream()
    if "study" in want:
        result["phases"]["study"] = _phase_study()
    if "train" in want:
        result["phases"]["train"] = _phase_train()

    result["journal"] = _narrate(result["phases"])
    result["elapsed_s"] = round(time.time() - t0, 1)
    result["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    _persist(result)
    try:
        from . import mesh
        mesh.publish("dream", "journal", result["journal"], salience=0.75)
    except Exception:  # noqa: BLE001
        pass
    return result


def _session() -> str:
    try:
        from . import marketclock
        return marketclock.session()
    except Exception:  # noqa: BLE001
        return "unknown"


def _persist(result: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        json.dump(result, open(_LAST, "w"), indent=2)
        # bounded journal log (keep last ~200 entries)
        line = json.dumps({"ts": result["ts"], "journal": result["journal"],
                           "elapsed_s": result.get("elapsed_s")})
        hist = []
        if os.path.exists(_JOURNAL):
            hist = open(_JOURNAL, encoding="utf-8").read().splitlines()[-199:]
        hist.append(line)
        open(_JOURNAL, "w", encoding="utf-8").write("\n".join(hist) + "\n")
    except Exception:  # noqa: BLE001
        pass


def last() -> dict:
    try:
        return json.load(open(_LAST))
    except Exception:  # noqa: BLE001
        return {"journal": "", "phases": {}, "ts": ""}


def journal(n: int = 20) -> list:
    try:
        lines = open(_JOURNAL, encoding="utf-8").read().splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:  # noqa: BLE001
        return []


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
