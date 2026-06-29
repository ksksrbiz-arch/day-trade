"""Durable, self-repairing agent runtime.

A cycle is a sequence of checkpointed STEPS (one per agent + a reflection step).
After each step the working state is checkpointed and the step marked done. If
the process crashes mid-cycle, the next start RESUMES from the exact step that
was in flight -- no work is lost or repeated. Each step is wrapped with retry
(self-repair); failures are recorded but never abort the cycle. Every step emits
a trace for full observability. Large results are offloaded to keep context
small, and a reflection step writes lessons to long-term memory each cycle.

  one durable cycle:   python -m trader.agents.runtime
  continuous:          python -m trader.agents.runtime --loop --every 900
  execute approvals:   python -m trader.agents.runtime --approvals
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

import sys
import time

from . import orchestrator as orch
from . import state, governor, memory, tools, cloudflare as cf

KIND = "autonomy"


def _retry(fn, attempts=2, base=1.5):
    last = None
    for i in range(attempts):
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001
            last = str(e)[:160]
            time.sleep(base * (i + 1))
    return None, last


def _agent_step(agent: dict, run_id: int) -> dict:
    t0 = time.time()
    res, err = _retry(lambda: orch.run_agent(agent))
    ms = int((time.time() - t0) * 1000)
    if err:
        state.trace(run_id, agent["name"], "agent", None, "failed", ms, err)
        return {"agent": agent["name"], "status": "failed", "error": err}
    status = res.get("status", "done")
    state.trace(run_id, agent["name"], "agent", res.get("tool"), status, ms,
                res.get("thought", "")[:200],
                {"result": str(res.get("result", res.get("note", "")))[:400]})
    return res


WATCH = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "UNH", "JNJ", "WMT", "META",
         "BTC/USD", "ETH/USD", "SOL/USD"]


def _mesh_step(run_id: int) -> dict:
    """Every layer speaks to the mesh + LTM, then it's available to all reasoners."""
    import time as _t
    t0 = _t.time()
    try:
        from .. import mesh
        out = mesh.snapshot()
        summ = f"published {out.get('published')} cross-layer insights"
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e)[:120]}; summ = out["error"]
    state.trace(run_id, "Mesh", "mesh", "snapshot", "done",
                int((_t.time() - t0) * 1000), summ)
    return {"agent": "Mesh", "tool": "mesh", "status": "done", "out": out}


def _predict_step(run_id: int) -> dict:
    """Prediction layer: resolve matured hypotheses, ingest new WSB ones,
    rebuild the decision matrix -- growing the platform's foresight."""
    import time as _t
    t0 = _t.time()
    try:
        from ..predict import engine as _pred
        out = _pred.cycle()
        summ = f"resolved {out.get('resolved')} new_plans {out.get('new_plans')} buckets {out.get('stats',{}).get('buckets')}"
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e)[:120]}; summ = out["error"]
    state.trace(run_id, "Predictor", "predict", "cycle", "done",
                int((_t.time() - t0) * 1000), summ)
    return {"agent": "Predictor", "tool": "predict", "status": "done", "out": out}


def _signal_capture(run_id: int) -> dict:
    """Record the system's current directional CALLS (confluence, ml, wsb) with
    a reference price, so they can be scored against forward outcomes later."""
    import time as _t
    t0 = _t.time(); n = 0
    try:
        from .. import sigtrack, alpha
        from ..crsp import query as crsp
        from ..ml import infer as ml
        syms = list(WATCH)
        try:
            from .. import wsb
            syms += [t["symbol"] for t in wsb.buzz().get("tickers", [])[:5] if "/" not in t["symbol"]]
        except Exception:  # noqa: BLE001
            pass
        for sym in dict.fromkeys(syms):
            try:
                bars = crsp.get_prices(sym, "2024-06-01", None)
                closes = [b["close"] for b in bars if b.get("close")]
                if len(closes) < 40:
                    continue
                last = closes[-1]
                conv = alpha.analyze(closes, symbol=sym)
                try:
                    from .. import backprop as _bp
                    _bp.log_decision(sym, conv.scores, ref_price=last, horizon=5,
                                     asset=('crypto' if '/' in sym else 'equity'))
                except Exception:  # noqa: BLE001
                    pass
                if conv.gate_pass and conv.side in ("buy", "sell"):
                    n += sigtrack.record("confluence", sym, conv.side, last, conv.composite)
                mls = ml.score_from_closes(closes)
                if mls is not None and abs(mls) >= 0.1:
                    n += sigtrack.record("ml", sym, "buy" if mls > 0 else "sell", last, mls)
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        state.trace(run_id, "SignalCapture", "signals", "record", "failed",
                    int((_t.time() - t0) * 1000), str(e)[:120])
        return {"agent": "SignalCapture", "tool": "signals", "status": "failed"}
    state.trace(run_id, "SignalCapture", "signals", "record", "done",
                int((_t.time() - t0) * 1000), f"recorded {n} signals")
    return {"agent": "SignalCapture", "tool": "signals", "status": "done", "recorded": n}


def _reflex_step(run_id: int) -> dict:
    """Deterministic, bounded self-tuning from evidence -- guarantees the desk
    acts on its diagnosis even when the LLM agents don't emit a clean proposal.
    Conservative single-step nudges; all clamped by the governor."""
    import time as _t
    t0 = _t.time()
    changes = []
    try:
        bt = tools.t_latest_backtest()
        brain = tools.t_brain_state()
        ml = tools.t_ml_card()
        ov = governor.load_overrides()
        edge = (bt or {}).get("edge_vs_spy_pct")
        regime = (brain or {}).get("regime", "neutral")
        ml_edge = (ml or {}).get("edge")

        cur_score = float(ov.get("CONFLUENCE_MIN_SCORE", 0.20))
        cur_conf = float(ov.get("MIN_CONFIDENCE", 0.60))

        # weak/negative backtest edge OR thin ML edge -> tighten selectivity
        if (edge is not None and edge < 0) or (ml_edge is not None and ml_edge < 0.03):
            r = governor.propose_param("Reflex", "CONFLUENCE_MIN_SCORE",
                                       round(cur_score + 0.03, 2),
                                       f"edge={edge} ml_edge={ml_edge} -> tighten selectivity")
            changes.append(r.get("summary"))
        # regime-adaptive DIRECTION (hunt finding: cash/long in chop+uptrend,
        # SHORT only in confirmed sustained downtrend = risk_off).
        if regime == "risk_off" and not ov.get("ALLOW_SHORT"):
            r = governor.propose_param("Reflex", "ALLOW_SHORT", True,
                                       "risk_off downtrend -> enable shorts to profit the drop")
            changes.append(r.get("summary"))
        elif regime in ("risk_on", "high_vol") and ov.get("ALLOW_SHORT"):
            r = governor.propose_param("Reflex", "ALLOW_SHORT", False,
                                       f"{regime} -> disable shorts (shorting an up/choppy tape loses)")
            changes.append(r.get("summary"))
        # stressed regime -> demand higher confidence
        if regime in ("high_vol", "risk_off"):
            r = governor.propose_param("Reflex", "MIN_CONFIDENCE",
                                       round(cur_conf + 0.03, 2),
                                       f"{regime} regime -> raise confidence floor")
            changes.append(r.get("summary"))
        # healthy edge -> gently relax (don't over-restrict in good conditions)
        if edge is not None and edge > 5 and regime == "risk_on" and cur_score > 0.20:
            r = governor.propose_param("Reflex", "CONFLUENCE_MIN_SCORE",
                                       round(cur_score - 0.02, 2),
                                       f"edge={edge} risk_on -> relax selectivity")
            changes.append(r.get("summary"))
    except Exception as e:  # noqa: BLE001
        changes = [f"reflex error: {str(e)[:80]}"]
    ms = int((_t.time() - t0) * 1000)
    state.trace(run_id, "Reflex", "reflex", "propose_param", "done", ms,
                "; ".join([c for c in changes if c]) or "no change warranted")
    return {"agent": "Reflex", "tool": "reflex", "status": "done", "changes": changes}


def _reflection_step(run_id: int, results: list[dict]) -> dict:
    """Improve over time: summarize the cycle, store a lesson in long-term memory."""
    t0 = time.time()
    acted = [r for r in results if r.get("tool") and r.get("status") == "done"]
    lines = [f"{r['agent']} used {r['tool']}: {str(r.get('result',''))[:80]}" for r in acted]
    digest = "Cycle summary:\n" + ("\n".join(lines) if lines else "no tool actions")
    lesson = cf.summarize(digest) if (cf.available() and lines) else digest[:280]
    try:
        memory.remember("LESSON: " + lesson, {"kind": "reflection"})
        state.kv_set("last_reflection", {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                         "lesson": lesson, "n_actions": len(acted)})
    except Exception:  # noqa: BLE001
        pass
    ms = int((time.time() - t0) * 1000)
    state.trace(run_id, "Reflector", "reflection", "memory", "done", ms, lesson[:200])
    return {"agent": "Reflector", "tool": "reflection", "status": "done", "lesson": lesson}


def _shadow_step(run_id: str) -> dict:
    """Shadow Lab: snapshot today's alt-config signals + resolve matured ones."""
    t0 = time.time()
    try:
        from .. import shadow
        r = shadow.run()
        summ = r.get("summary", "shadow lab")
    except Exception as e:  # noqa: BLE001
        summ = f"shadow error: {str(e)[:90]}"
    ms = int((time.time() - t0) * 1000)
    state.trace(run_id, "ShadowLab", "run", "control", "done", ms, summ[:200])
    return {"agent": "ShadowLab", "tool": "run", "status": "done", "summary": summ}


def _autonomy_step(run_id: str) -> dict:
    """Platform-wide self-tuning sweep: evidence-guarded, mode-gated."""
    t0 = time.time()
    try:
        from .. import autonomy
        res = autonomy.sweep()
        if res.get("disabled"):
            summ = f"autonomy {res.get('mode')} (no mutation)"
        else:
            ap = sum(1 for x in res["results"] if x.get("status") == "applied")
            pr = sum(1 for x in res["results"] if x.get("status") == "proposed")
            summ = f"autonomy {res.get('mode')}: {ap} applied, {pr} proposed"
    except Exception as e:  # noqa: BLE001
        summ = f"autonomy error: {str(e)[:90]}"
    ms = int((time.time() - t0) * 1000)
    state.trace(run_id, "Autonomy", "sweep", "control", "done", ms, summ[:200])
    return {"agent": "Autonomy", "tool": "sweep", "status": "done", "summary": summ}


def _alerts_step(run_id: str) -> dict:
    """Evaluate alert rules; surface the few things worth a human's attention."""
    t0 = time.time()
    try:
        from .. import alerts
        res = alerts.fire()
        summ = f"alerts: {res.get('new', 0)} new"
    except Exception as e:  # noqa: BLE001
        summ = f"alerts error: {str(e)[:90]}"
    ms = int((time.time() - t0) * 1000)
    state.trace(run_id, "Alerts", "fire", "control", "done", ms, summ[:200])
    return {"agent": "Alerts", "tool": "fire", "status": "done", "summary": summ}


def run_cycle() -> dict:
    """Run (or resume) ONE durable cycle across all agents + reflection."""
    try:
        from .. import sigtrack
        sigtrack.reconcile()
    except Exception:  # noqa: BLE001
        pass
    roster = orch.ROSTER
    step_names = ["mesh"] + [a["name"] for a in roster] + ["predict", "signal_capture", "reflex", "reflection", "shadow", "autonomy", "alerts"]

    resume = state.resumable_run(KIND)
    if resume:
        run_id, cursor = resume
        blackboard = state.load_checkpoint(run_id)
    else:
        run_id = state.start_run(KIND, step_names, "autonomy cycle")
        cursor, blackboard = 0, {"results": []}

    results = blackboard.get("results", [])
    for idx in range(cursor, len(step_names)):
        if state.step_status(run_id, idx) == "done":
            continue
        state.mark_step(run_id, idx, "running")
        if idx == 0:
            r = _mesh_step(run_id)
        elif idx <= len(roster):
            r = _agent_step(roster[idx - 1], run_id)
        elif idx == len(roster) + 1:
            r = _predict_step(run_id)
        elif idx == len(roster) + 2:
            r = _signal_capture(run_id)
        elif idx == len(roster) + 3:
            r = _reflex_step(run_id)
        elif idx == len(roster) + 4:
            r = _reflection_step(run_id, results)
        elif idx == len(roster) + 5:
            r = _shadow_step(run_id)
        elif idx == len(roster) + 6:
            r = _autonomy_step(run_id)
        else:
            r = _alerts_step(run_id)
        results.append(r)
        # context management: keep only compact fields on the blackboard
        blackboard["results"] = [{k: v for k, v in x.items()
                                  if k in ("agent", "tool", "status", "thought")}
                                 for x in results][-12:]
        state.save_checkpoint(run_id, blackboard)
        state.mark_step(run_id, idx, "done", {"agent": r.get("agent"), "tool": r.get("tool")})

    state.finish_run(run_id, "done")
    pend = len(state.pending_approvals())
    return {"run_id": run_id, "steps": len(step_names), "results": results,
            "pending_approvals": pend}


def execute_approved() -> list[dict]:
    """Run any approvals a human has APPROVED (and clear rejected). HITL resume."""
    done = []
    c = state.conn()
    rows = c.execute("SELECT * FROM approvals WHERE status='approved'").fetchall()
    c.close()
    for r in rows:
        import json as _j
        payload = _j.loads(r["payload"]) if r["payload"] else {}
        args = payload.get("args", {})
        result = tools.call(r["action"], agent=r["agent"], **args)
        governor.record_action(r["agent"], r["action"],
                               f"executed approved {r['action']} -> {str(result)[:90]}",
                               {"approval_id": r["id"]})
        # mark consumed so it doesn't run twice
        cc = state.conn()
        cc.execute("UPDATE approvals SET status='executed' WHERE id=?", (r["id"],))
        cc.commit(); cc.close()
        done.append({"approval_id": r["id"], "action": r["action"], "result": result})
    return done


def main():
    if "--approvals" in sys.argv:
        print("executed approvals:", execute_approved())
        return
    loop = "--loop" in sys.argv
    every = 900
    for i, a in enumerate(sys.argv):
        if a == "--every" and i + 1 < len(sys.argv):
            every = int(sys.argv[i + 1])
    print(f"[runtime] durable autonomy loop={loop} every={every}s")
    while True:
        # first, run anything a human approved since last cycle
        try:
            ex = execute_approved()
            if ex:
                print(f"[runtime] executed {len(ex)} approved actions")
        except Exception as e:  # noqa: BLE001
            print("[runtime] approval-exec error:", e)
        try:
            out = run_cycle()
            print(f"[runtime] cycle {out['run_id']} done; pending_approvals={out['pending_approvals']}")
        except Exception as e:  # noqa: BLE001
            print("[runtime] cycle error (will retry next tick):", str(e)[:160])
        if not loop:
            break
        time.sleep(every)


if __name__ == "__main__":
    main()
