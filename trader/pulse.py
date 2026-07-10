"""Heartbeat: keep the platform visibly alive between the slow daemon cycles.

The rich activity sources (agent traces, council votes, trades) go quiet when
the market is closed, and the autonomy sweep only runs every few minutes -- so
the /brain mesh looks still even though real background work is happening. This
heartbeat publishes a rotating set of REAL, already-computed state snapshots to
the mesh every ~30s. Each publish becomes a live fire-event (particle) in the
network view, so the graph reflects the system's steady pulse.

Honest: every line is a cached read of genuine internal state (regime, mood,
self-built beliefs, last dream, learned weights, forecast, factor leadership,
autonomy decisions). Nothing here is fabricated activity -- it is the system
narrating what it already knows, on a clock.
"""
from __future__ import annotations

import time


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def beat() -> dict:
    """Publish one heartbeat's worth of real state to the mesh. Returns a count."""
    from . import mesh
    pub = 0

    # regime + posture (brain)
    def _regime():
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
        pe = market_brain.cached_posture("equity")
        return mesh.publish("brain", "regime",
                            f"regime {reg}; equity {pe.get('bias','')} x{pe.get('size_mult','')}",
                            salience=0.55)
    pub += _safe(_regime) or 0

    # internal state / mood (desk)
    def _mood():
        from . import psyche
        st = psyche.state()
        return mesh.publish("desk", "mood",
                            f"mood {st.get('mood','')} · val {st.get('valence',0):+.2f} "
                            f"cur {st.get('curiosity',0):.2f} str {st.get('stress',0):.2f}",
                            salience=0.5)
    pub += _safe(_mood) or 0

    # self-built beliefs steering the voices (confluence)
    def _beliefs():
        from . import beliefs
        mult = beliefs.voice_multipliers(None)
        if not mult:
            return 0
        top = sorted(mult.items(), key=lambda kv: abs(kv[1] - 1.0), reverse=True)[:3]
        txt = ", ".join(f"{k} x{v:.2f}" for k, v in top)
        return mesh.publish("confluence", "beliefs", f"belief steering: {txt}", salience=0.5)
    pub += _safe(_beliefs) or 0

    # transformer forecast (attention/prediction)
    def _forecast():
        from . import tnet
        fc = tnet.forecast("SPY")
        if "error" in fc:
            return 0
        return mesh.publish("prediction", "forecast",
                            f"SPY {fc['direction']} p(up) {fc['prob_up']:.0%} conf {fc['confidence']:.0%}",
                            symbol="SPY", salience=0.55)
    pub += _safe(_forecast) or 0

    # learned confluence weights (ml)
    def _weights():
        from . import backprop
        bc = backprop.card()
        if not bc.get("trained"):
            return 0
        emp = bc.get("emphasis", {}) or {}
        top = sorted(emp.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return mesh.publish("ml", "weights",
                            "learned emphasis: " + ", ".join(f"{k} {v:.0%}" for k, v in top),
                            salience=0.5)
    pub += _safe(_weights) or 0

    # factor leadership (confluence)
    def _factors():
        from . import factors
        rep = factors.report() if hasattr(factors, "report") else None
        if not rep:
            return 0
        lead = rep.get("leader") or (rep.get("factors") or [{}])[0].get("name")
        if not lead:
            return 0
        return mesh.publish("confluence", "factors", f"factor leadership: {lead}", salience=0.45)
    pub += _safe(_factors) or 0

    # last dream journal (memory) -- only while it's fresh
    def _dream():
        from . import dream, marketclock
        if marketclock.is_open():
            return 0
        last = dream.last()
        j = (last or {}).get("journal")
        if not j:
            return 0
        return mesh.publish("memory", "dream", j[:140], salience=0.6)
    pub += _safe(_dream) or 0

    # most recent autonomy decision (mesh/runtime)
    def _autonomy():
        from . import autonomy
        aud = autonomy.recent_audit(1)
        if not aud:
            return 0
        e = aud[0]
        return mesh.publish("mesh", "autonomy",
                            f"{e.get('action','')}: {e.get('status','')} — {e.get('reason','')[:80]}",
                            salience=0.5)
    pub += _safe(_autonomy) or 0

    # awareness one-liner (reasoning)
    def _aware():
        from . import awareness
        b = awareness.brief(1)
        if not b:
            return 0
        return mesh.publish("reasoning", "awareness", b[:140], salience=0.45)
    pub += _safe(_aware) or 0

    return {"published": pub, "ts": time.strftime("%H:%M:%S")}


def loop(every: float = 30.0) -> None:
    print(f"[pulse] heartbeat every {every}s")
    while True:
        try:
            r = beat()
            print(f"[pulse] {r['ts']} published {r['published']} state events")
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            print(f"[pulse] error (continuing): {e}")
        time.sleep(every)


if __name__ == "__main__":
    import sys
    every = 30.0
    if "--every" in sys.argv:
        try:
            every = float(sys.argv[sys.argv.index("--every") + 1])
        except Exception:  # noqa: BLE001
            pass
    if "--loop" in sys.argv:
        loop(every)
    else:
        import json
        print(json.dumps(beat(), indent=2))
