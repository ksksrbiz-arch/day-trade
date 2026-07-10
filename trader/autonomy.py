"""Platform-wide autonomy controller.

Generalizes the Performance Auditor's pattern -- read REALIZED forward evidence,
act only when it's matured, guard every change with hard bounds -- across the
whole operating scheme. A single controller evaluates a registry of guarded
self-tuning ACTIONS each cycle and, depending on the global autonomy mode, either
proposes or applies them. Every decision is audited to the mesh.

Safety model (two tiers + kill-switch):
  mode = "off"      -> no autonomous mutation at all.
  mode = "propose"  -> eligible actions are PROPOSED only (logged for review);
                       nothing mutates. (default)
  mode = "auto"     -> actions flagged auto_safe (conservative, reversible) APPLY
                       automatically; everything else is still proposed.
  kill_switch=True  -> hard stop; overrides mode, blocks all mutation.

Each action is honest: when its evidence is still maturing it reports
"blocked" with a reason and does nothing -- so turning autonomy on today changes
nothing until forward outcomes actually justify a change.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "autonomy"))
POLICY = os.path.join(_DATA, "policy.json")
AUDIT = os.path.join(_DATA, "audit.jsonl")

MODES = ("off", "propose", "auto")


# ============================ policy ====================================== #
def _regime_now() -> str:
    try:
        from . import market_brain
        return market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        return "neutral"


def _gate_hours(calm: float, stressed: float) -> float:
    """Adaptive cadence: explore faster when calm, slow down under stress."""
    return stressed if _regime_now() in ("high_vol", "risk_off") else calm


def _default_mode() -> str:
    m = os.getenv("AUTONOMY", "auto").strip().lower()
    return m if m in MODES else "auto"


def policy() -> dict:
    try:
        with open(POLICY) as f:
            d = json.load(f)
        return {"mode": d.get("mode", _default_mode()),
                "kill_switch": bool(d.get("kill_switch", False)),
                "updated": d.get("updated", "")}
    except Exception:  # noqa: BLE001
        return {"mode": _default_mode(), "kill_switch": False, "updated": ""}


def circuit_breaker_check() -> dict:
    """HARD safety rail BELOW the autonomy layer: trip the kill switch if paper
    equity draws down past AUTONOMY_MAX_DD% from its high-water mark. Autonomy
    cannot tune its way past this -- only a human re-enables it via set_policy."""
    try:
        max_dd = float(os.getenv("AUTONOMY_MAX_DD", "15"))
        from . import config
        from .broker import AlpacaBroker
        from .agents import state
        cfg = config.load()
        if not getattr(cfg, "alpaca_key", ""):
            return {"checked": False, "reason": "no broker keys"}
        eq = float(AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret, paper=True).account_value())
        hw = float(state.kv_get("autonomy_hw", 0.0) or 0.0)
        if eq > hw:
            state.kv_set("autonomy_hw", eq); hw = eq
        dd = (hw - eq) / hw * 100.0 if hw > 0 else 0.0
        # append to the durable equity curve (P&L sparkline source), capped + dedup'd
        try:
            import json as _json
            hist = state.kv_get("equity_hist", "[]")
            pts = _json.loads(hist) if isinstance(hist, str) else (hist or [])
            now = int(_t.time())
            if not pts or now - int(pts[-1][0]) >= 900 or abs(float(pts[-1][1]) - eq) > 1.0:
                pts.append([now, round(eq, 2)])
                state.kv_set("equity_hist", _json.dumps(pts[-240:]))
        except Exception:  # noqa: BLE001
            pass
        if dd >= max_dd and not policy()["kill_switch"]:
            set_policy(kill_switch=True)
            _audit({"action": "circuit_breaker", "status": "applied",
                    "reason": f"paper drawdown {dd:.1f}% >= {max_dd}% -> AUTONOMY HALTED"})
            return {"checked": True, "tripped": True, "dd": round(dd, 2)}
        return {"checked": True, "tripped": False, "dd": round(dd, 2), "hw": round(hw, 2), "eq": round(eq, 2)}
    except Exception as e:  # noqa: BLE001
        return {"checked": False, "error": str(e)[:100]}


def set_policy(mode: str | None = None, kill_switch: bool | None = None) -> dict:
    p = policy()
    if mode in MODES:
        p["mode"] = mode
    if kill_switch is not None:
        p["kill_switch"] = bool(kill_switch)
    p["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(_DATA, exist_ok=True)
    with open(POLICY, "w") as f:
        json.dump(p, f, indent=2)
    return p


def _audit(entry: dict):
    os.makedirs(_DATA, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass
    if entry.get("status") in ("applied", "proposed"):
        try:
            from . import mesh
            mesh.publish("autonomy", entry["status"], f"{entry['action']}: {entry['reason']}",
                         salience=0.6 if entry["status"] == "applied" else 0.5)
        except Exception:  # noqa: BLE001
            pass


def recent_audit(n: int = 30) -> list[dict]:
    if not os.path.exists(AUDIT):
        return []
    out = []
    for ln in open(AUDIT, encoding="utf-8").read().splitlines()[-n:]:
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                pass
    return list(reversed(out))


# ===================== evidence helpers =================================== #
def _edge_source(name: str):
    try:
        from . import edge
        for s in edge.report().get("sources", []):
            if s["source"] == name:
                return s
    except Exception:  # noqa: BLE001
        pass
    return None


def _attr_voices():
    try:
        from . import attribution
        return attribution.report().get("voices", [])
    except Exception:  # noqa: BLE001
        return []


def _cur_param(name: str, default):
    try:
        from .agents import governor
        ov = governor.load_overrides()
        if name in ov:
            return ov[name]
    except Exception:  # noqa: BLE001
        pass
    return default


# ===================== guarded actions =================================== #
# Each action: evaluate() -> {eligible, reason, proposal}; apply(proposal) -> result.
def _ev_prune_voice():
    from . import voices
    ov = voices.overrides()
    for v in _attr_voices():
        if v.get("verdict") == "unprofitable" and v["voice"] not in ov["muted"]:
            return {"eligible": True, "voice": v["voice"],
                    "reason": f"{v['voice']} attribution unprofitable "
                              f"({v.get('attributed_return_pct')}%) -> mute",
                    "proposal": {"kind": "mute", "voice": v["voice"]}}
    return {"eligible": False, "reason": "no voice is proven unprofitable on resolved data"}


def _ap_prune_voice(p):
    from . import voices
    voices.set_mute(p["voice"], True)
    return {"muted": p["voice"]}


def _ev_promote_voice():
    from . import voices
    ov = voices.overrides()
    best = None
    for v in _attr_voices():
        if v.get("verdict") == "profitable" and v["voice"] not in ov["pinned"]:
            if best is None or (v.get("attributed_return_pct") or 0) > (best.get("attributed_return_pct") or 0):
                best = v
    if best:
        eff = next((x["effective"] for x in voices.summary()["voices"] if x["voice"] == best["voice"]), 0.2)
        return {"eligible": True, "voice": best["voice"],
                "reason": f"{best['voice']} proven profitable -> pin weight {eff}",
                "proposal": {"kind": "pin", "voice": best["voice"], "weight": eff}}
    return {"eligible": False, "reason": "no voice is proven profitable on resolved data"}


def _ap_promote_voice(p):
    from . import voices
    voices.set_pin(p["voice"], float(p["weight"]))
    return {"pinned": p["voice"], "weight": p["weight"]}


def _ev_tighten_selectivity():
    """If the live confluence signal underperforms a coin flip on enough resolved
    calls, raise CONFLUENCE_MIN_SCORE (more conservative). Auto-safe."""
    s = _edge_source("confluence")
    if not s or (s.get("resolved") or 0) < 20 or s.get("hit_rate") is None:
        return {"eligible": False, "reason": "confluence edge still maturing (<20 resolved)"}
    if s["hit_rate"] >= 0.50:
        return {"eligible": False, "reason": f"confluence hit-rate {s['hit_rate']:.0%} is not underperforming"}
    cur = float(_cur_param("CONFLUENCE_MIN_SCORE", 0.20))
    new = round(min(0.45, cur + 0.05), 2)
    if new <= cur:
        return {"eligible": False, "reason": "CONFLUENCE_MIN_SCORE already at bound"}
    return {"eligible": True,
            "reason": f"confluence hit-rate {s['hit_rate']:.0%} < 50% over {s['resolved']} -> "
                      f"raise CONFLUENCE_MIN_SCORE {cur}->{new}",
            "proposal": {"kind": "param", "name": "CONFLUENCE_MIN_SCORE", "value": new}}


def _ev_risk_guard():
    """In a stressed regime, ensure MIN_CONFIDENCE is defensively high. Auto-safe."""
    try:
        from . import market_brain
        regime = market_brain.cached_regime("neutral")
    except Exception:  # noqa: BLE001
        regime = "neutral"
    if regime not in ("high_vol", "risk_off"):
        return {"eligible": False, "reason": f"regime {regime} not stressed"}
    cur = float(_cur_param("MIN_CONFIDENCE", 0.55))
    if cur >= 0.65:
        return {"eligible": False, "reason": f"MIN_CONFIDENCE already defensive ({cur})"}
    return {"eligible": True,
            "reason": f"stressed regime ({regime}) -> raise MIN_CONFIDENCE {cur}->0.65",
            "proposal": {"kind": "param", "name": "MIN_CONFIDENCE", "value": 0.65}}


def _ap_param(p):
    from .agents import governor
    return governor.propose_param("autonomy", p["name"], p["value"], "autonomy controller")


def _ev_enable_cortex():
    """Enable the neural core in live confluence once its Shadow Lab book has
    beaten the live (linear) book over enough resolved trades."""
    from . import cortex
    if cortex.enabled():
        return {"eligible": False, "reason": "neural core already enabled"}
    if not cortex.card().get("trained"):
        return {"eligible": False, "reason": "neural core not trained yet"}
    try:
        from . import shadow
        books = {b["book"]: b for b in shadow.standings().get("books", [])}
    except Exception:  # noqa: BLE001
        return {"eligible": False, "reason": "shadow standings unavailable"}
    cx, lv = books.get("cortex"), books.get("live")
    if not cx or (cx.get("trades") or 0) < 20:
        return {"eligible": False, "reason": "cortex shadow book still maturing (<20 trades)"}
    if not lv or cx["total_return_pct"] <= (lv.get("total_return_pct") or 0):
        return {"eligible": False, "reason": "cortex shadow not beating the live blend yet"}
    return {"eligible": True,
            "reason": f"cortex shadow {cx['total_return_pct']:+.2f}% > live "
                      f"{lv.get('total_return_pct'):+.2f}% over {cx['trades']} trades -> enable neural core",
            "proposal": {"kind": "cortex_enable"}}


def _ap_enable_cortex(p):
    from . import cortex
    return cortex.set_enabled(True)


# ---- self-optimization / self-maintenance ---- #
def _age_h(path) -> float | None:
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except Exception:  # noqa: BLE001
        return None


def _lines(path) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:  # noqa: BLE001
        return 0


def _ev_retrain_stale_ml():
    """Retrain the ML model when it's missing or older than 24h. Auto-safe
    (champion/challenger means a retrain can only improve the live model)."""
    try:
        from .ml.model import MODEL_PATH
    except Exception:  # noqa: BLE001
        return {"eligible": False, "reason": "ml model module unavailable"}
    age = _age_h(MODEL_PATH)
    if age is None:
        return {"eligible": True, "reason": "no ML model yet -> train", "proposal": {"kind": "retrain_ml"}}
    if age < 24:
        return {"eligible": False, "reason": f"ML model fresh ({age:.1f}h old)"}
    # A failed challenge leaves the champion file (and its mtime) unchanged, so
    # gating on the champion age alone retrains every single sweep. Gate on the
    # last ATTEMPT instead so a stale-but-unbeatable champion is retried on a
    # cadence, not in a tight loop.
    last = _age_h(os.path.join(_DATA, "ml_retrain_last"))
    cd = _gate_hours(6, 8)
    if last is not None and last < cd:
        return {"eligible": False, "reason": f"retrain attempted recently ({last:.1f}h < {cd}h)"}
    return {"eligible": True, "reason": f"ML model stale ({age:.0f}h) -> retrain (champion-gated)",
            "proposal": {"kind": "retrain_ml"}}


def _ap_retrain_ml(p):
    from .ml.train import train_once
    import time as _t
    res = train_once()
    try:
        os.makedirs(_DATA, exist_ok=True)
        open(os.path.join(_DATA, "ml_retrain_last"), "w").write(
            _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
    except Exception:  # noqa: BLE001
        pass
    return res


def _ev_recalibrate_tnet():
    """Recalibrate the transformer when calibration is missing/stale and enough
    forecasts have matured. Auto-safe."""
    from . import tnet
    n = _lines(tnet._FLOG)
    if n < 30:
        return {"eligible": False, "reason": f"only {n} logged forecasts (need 30)"}
    age = _age_h(tnet._CALIB)
    if age is not None and age < 24:
        return {"eligible": False, "reason": f"calibration fresh ({age:.1f}h)"}
    return {"eligible": True, "reason": "calibration missing/stale -> recalibrate",
            "proposal": {"kind": "recalibrate"}}


def _ap_recalibrate(p):
    from . import tnet
    return tnet.calibrate()


def _ev_prune_data_logs():
    """Keep growth-prone logs bounded. Auto-safe maintenance."""
    from . import tnet
    fn = _lines(tnet._FLOG)
    if fn <= 4000:
        return {"eligible": False, "reason": f"forecast log healthy ({fn} rows)"}
    return {"eligible": True, "reason": f"forecast log large ({fn} rows) -> prune",
            "proposal": {"kind": "prune"}}


def _ap_prune_logs(p):
    from . import tnet
    return {"forecast_rows": tnet.prune_logs()}


def _ev_relax_selectivity():
    """If confluence is over-gating yet still accurate, loosen selectivity a notch.
    Needs review (loosening risk)."""
    s = _edge_source("confluence")
    if not s or (s.get("resolved") or 0) < 20 or s.get("hit_rate") is None:
        return {"eligible": False, "reason": "confluence edge still maturing (<20 resolved)"}
    if s["hit_rate"] < 0.60:
        return {"eligible": False, "reason": f"confluence hit-rate {s['hit_rate']:.0%} not strong enough to loosen"}
    cur = float(_cur_param("CONFLUENCE_MIN_SCORE", 0.20))
    new = round(max(0.10, cur - 0.03), 2)
    if new >= cur:
        return {"eligible": False, "reason": "CONFLUENCE_MIN_SCORE already at floor"}
    return {"eligible": True,
            "reason": f"confluence hit-rate {s['hit_rate']:.0%} over {s['resolved']} but gating hard -> "
                      f"lower CONFLUENCE_MIN_SCORE {cur}->{new}",
            "proposal": {"kind": "param", "name": "CONFLUENCE_MIN_SCORE", "value": new}}


def _cortex_samples() -> int:
    """Cheap count of resolved-decision training rows available to the cortex."""
    try:
        from . import backprop
        X, _y = backprop.build_dataset()
        return int(len(X))
    except Exception:  # noqa: BLE001
        return 0


def _ev_train_cortex():
    """Train the neural core when it's untrained (or weekly-stale) and enough
    resolved decisions exist. Auto-safe: training is champion/challenger gated,
    so a new ensemble only goes live if it holds validation accuracy. Throttled
    via the card's age so a sweep can't retrain every cycle."""
    from . import cortex
    age = _age_h(cortex.CARD)
    trained = cortex.card().get("trained")
    if trained and (age is None or age < 168):
        return {"eligible": False, "reason": f"cortex trained & fresh ({age}h)"}
    if not trained and age is not None and age < 6:
        return {"eligible": False, "reason": "cortex train recently attempted (<6h)"}
    n = _cortex_samples()
    if n < 30:
        return {"eligible": False, "reason": f"only {n} resolved decisions (need 30)"}
    return {"eligible": True,
            "reason": f"cortex {'stale' if trained else 'untrained'} with {n} samples -> train (champion-gated)",
            "proposal": {"kind": "train_cortex"}}


def _ap_train_cortex(p):
    from . import cortex
    return cortex.train()


def _ev_run_backtest():
    """Time-gated (every ~4h): run a walk-forward backtest vs SPY on Alpaca data,
    so the /api/backtest surface + the Quant Researcher agent have real data."""
    try:
        from .agents import state
        import time as _t
        last = float(state.kv_get("wf_last", 0) or 0)
        if _t.time() - last < _gate_hours(3, 6) * 3600:
            return {"eligible": False, "reason": f"cooldown (regime-adaptive gate, {_regime_now()})"}
        return {"eligible": True, "reason": "due for a walk-forward backtest", "proposal": {}}
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"eval error: {str(e)[:60]}"}


def _ap_run_backtest(p):
    from . import walkforward as wf
    from .agents import state
    import time as _t
    r = wf.run(days=400, source="alpaca", out="latest.json")
    state.kv_set("wf_last", _t.time())
    m = r.get("meta", {})
    return {"symbols": m.get("symbols"), "edge_vs_spy_pct": r.get("edge_vs_spy_pct"),
            "error": m.get("error")}


def _ev_tune_aggression():
    """Time-gated (~1h): teach the desk its own risk appetite -- raise aggression
    when it's demonstrably working (positive edge, low drawdown), cut it when
    losing. Bounded [0.2, 1.2]. This is 'aggressive for profit, but self-correcting'."""
    try:
        from .agents import state
        import time as _t
        last = float(state.kv_get("aggr_last", 0) or 0)
        if _t.time() - last < 3600:
            return {"eligible": False, "reason": "cooldown (<1h)"}
        return {"eligible": True, "reason": "tune aggression from realized edge/drawdown", "proposal": {}}
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"eval error: {str(e)[:60]}"}


def _ap_tune_aggression(p):
    from .agents import state
    import time as _t
    cur = float(state.kv_get("aggression", os.getenv("AGGRESSION", "0.6")) or 0.6)
    edge, dd = 0.0, 0.0
    try:
        from .ml import infer
        edge = float(infer.model_card().get("edge", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        b = circuit_breaker_check()
        dd = float(b.get("dd") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    if dd >= 8:
        nxt = cur - 0.15                    # drawing down -> pull risk in
    elif edge > 0.03:
        nxt = cur + 0.10                    # real edge + safe -> lean in more
    elif edge <= 0.0:
        nxt = cur - 0.10                    # no edge -> back off
    else:
        nxt = cur
    # blend in the hypothesis lab's compounding-backtest recommendation
    reco = None
    try:
        reco = state.kv_get("aggression_reco", None)
        if reco is not None:
            nxt = 0.6 * nxt + 0.4 * float(reco)
    except Exception:  # noqa: BLE001
        pass
    nxt = max(0.2, min(1.2, round(nxt, 2)))
    state.kv_set("aggression", nxt); state.kv_set("aggr_last", _t.time())
    return {"aggression": nxt, "from": round(cur, 2), "edge": round(edge, 4),
            "dd": round(dd, 2), "lab_reco": reco}


def _ev_discover_strategy():
    """Time-gated (every ~2h): run the hypothesis lab (generate -> backtest ->
    promote). Cheap here -- the heavy backtest sweep runs in apply()."""
    try:
        from .agents import state
        import time as _t
        last = float(state.kv_get("hypolab_last", 0) or 0)
        if _t.time() - last < _gate_hours(1, 3) * 3600:
            return {"eligible": False, "reason": f"cooldown (regime-adaptive gate, {_regime_now()})"}
        return {"eligible": True, "reason": "due for a hypothesis/backtest sweep",
                "proposal": {"n": 6}}
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"eval error: {str(e)[:60]}"}


def _ap_discover_strategy(p):
    from . import hypolab
    from .agents import state
    import time as _t
    r = hypolab.run(int((p or {}).get("n", 6)))
    state.kv_set("hypolab_last", _t.time())
    return {"winner": r.get("winner"), "promoted": r.get("promoted"),
            "baseline_vs": r.get("baseline_vs_benchmark")}


def _ev_bootstrap_brain():
    """One-shot cold-start: when the fusion decision store is nearly empty the
    cortex + confluence learners cannot train at all. This backfills thousands of
    point-in-time historical decisions so they can train immediately, instead of
    waiting weeks for live decisions to mature. Self-limiting: once the store has
    >= 30 rows this is permanently ineligible."""
    try:
        from . import pretrain
        # cheap short-circuit: don't re-run within a day of the last cold-start
        age = _age_h(pretrain._STATE)
        if age is not None and age < 24:
            return {"eligible": False, "reason": f"cold-start attempted recently ({age}h)"}
        # gate on RESOLVED training rows (what cortex/confluence actually consume),
        # not merely logged rows -- recent live decisions log but do not resolve yet
        n = _cortex_samples()
        if n >= 30:
            return {"eligible": False, "reason": f"fusion store warm ({n} resolved) -> no cold-start needed"}
        return {"eligible": True,
                "reason": f"fusion store cold ({n} resolved) -> backfill history to wake the brain",
                "proposal": {"kind": "bootstrap_brain"}}
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"eval error: {str(e)[:80]}"}


def _ap_bootstrap_brain(p):
    from . import pretrain
    return pretrain.run(max_symbols=24, step=5, horizon=10, warmup=70)


def _ev_scan_catalysts():
    """Periodically scan the universe for momentum catalysts and arm the
    watch->wait->strike list, so the desk always has fresh, price-confirmed
    setups queued. Auto-safe: arming only queues a watch; the strike still
    requires price confirmation + the confluence gate."""
    try:
        from . import scanner  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"scanner unavailable: {str(e)[:60]}"}
    last = _age_h(os.path.join(_DATA, "scan_last"))
    gate = _gate_hours(3, 6)
    if last is not None and last < gate:
        return {"eligible": False, "reason": f"cooldown ({last}h < {gate}h scan gate)"}
    return {"eligible": True, "reason": "scan universe -> arm momentum catalysts",
            "proposal": {"kind": "scan_catalysts"}}


def _ap_scan_catalysts(p):
    from . import scanner
    from .agents import state
    import time as _t
    mn, n = 0.66, 6
    try:                                          # curiosity widens exploration
        from . import psyche
        mod = psyche.state().get("modulation", {})
        mn = float(mod.get("scan_min_conf", 0.66))
        n = int(round(6 * float(mod.get("exploration", 1.0))))
    except Exception:  # noqa: BLE001
        pass
    res = scanner.arm_top(n=max(6, min(12, n)), min_conf=mn)
    try:
        state.kv_set("scan_last", _t.time())
        os.makedirs(_DATA, exist_ok=True)
        open(os.path.join(_DATA, "scan_last"), "w").write(
            _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
    except Exception:  # noqa: BLE001
        pass
    return res


def _ev_calibrate_meta():
    """Train the probability calibrator + meta-labeler on the resolved-decision
    store once enough matured decisions exist and the model is stale/untrained.
    Auto-safe: fits only on realized outcomes, influences sizing not gating."""
    try:
        from . import calibrate  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"calibrate unavailable: {str(e)[:60]}"}
    n = _cortex_samples()
    if n < 30:
        return {"eligible": False, "reason": f"only {n} resolved decisions (need 30)"}
    age = _age_h(calibrate.META_PATH)
    if age is not None and age < 72:
        return {"eligible": False, "reason": f"calibrator fresh ({age}h)"}
    return {"eligible": True, "reason": f"train meta-labeler + calibration on {n} decisions",
            "proposal": {"kind": "calibrate_meta"}}


def _ap_calibrate_meta(p):
    from . import calibrate
    return calibrate.train()


def _ev_reflect():
    """Introspection cadence: periodically the system reflects on its internal
    state + recent experience and forms/updates durable beliefs in long-term
    memory (autonomous knowledge-building). Auto-safe: writes to memory only,
    never touches trading. Runs more often when curious, less when calm."""
    try:
        from . import psyche  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"psyche unavailable: {str(e)[:60]}"}
    last = _age_h(os.path.join(_DATA, "reflect_last"))
    gate = _gate_hours(2, 4)
    if last is not None and last < gate:
        return {"eligible": False, "reason": f"reflected recently ({last}h < {gate}h)"}
    return {"eligible": True, "reason": "reflect on state + update self-knowledge",
            "proposal": {"kind": "reflect"}}


def _ap_reflect(p):
    from . import psyche
    import time as _t
    res = psyche.reflect()
    try:
        os.makedirs(_DATA, exist_ok=True)
        open(os.path.join(_DATA, "reflect_last"), "w").write(
            _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
    except Exception:  # noqa: BLE001
        pass
    return res


def _ev_dream():
    """Sleep-phase consolidation: when the US market is CLOSED, run the dream
    cycle -- replay + resolve episodes, consolidate/prune beliefs, counterfactual
    replay over real history, curiosity web study, and offline retraining. Only
    eligible while the market is closed so it never competes with live trading.
    Auto-safe: memory + training stores only, never the broker."""
    try:
        from . import marketclock
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"marketclock unavailable: {str(e)[:60]}"}
    if marketclock.is_open():
        return {"eligible": False, "reason": "market open -- dreaming waits for close"}
    last = _age_h(os.path.join(_DATA, "dream_last"))
    gate = 3.0  # a few consolidation passes per overnight, bounded + idempotent
    if last is not None and last < gate:
        return {"eligible": False, "reason": f"dreamed recently ({last}h < {gate}h)"}
    sess = marketclock.session()
    return {"eligible": True, "reason": f"market {sess} -- run the dream cycle",
            "proposal": {"kind": "dream"}}


def _ap_dream(p):
    from . import dream
    import time as _t
    res = dream.run(reason="autonomy: market closed")
    try:
        os.makedirs(_DATA, exist_ok=True)
        open(os.path.join(_DATA, "dream_last"), "w").write(
            _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
    except Exception:  # noqa: BLE001
        pass
    return {"journal": res.get("journal"), "elapsed_s": res.get("elapsed_s"),
            "session": res.get("session")}


# ---- free-model cognition actions (LLM put to work, gated + auto-safe) ---- #
def _llm_ready():
    try:
        from . import reasoner
        return reasoner.available()
    except Exception:  # noqa: BLE001
        return False


def _cog_gate(name: str, gate: float):
    """Shared eligibility: free models up + cadence elapsed."""
    if not _llm_ready():
        return None, {"eligible": False, "reason": "free models unavailable"}
    last = _age_h(os.path.join(_DATA, f"{name}_last"))
    if last is not None and last < gate:
        return None, {"eligible": False, "reason": f"ran recently ({last}h < {gate}h)"}
    return True, None


def _cog_apply(name: str, fn):
    import time as _t
    res = fn()
    try:
        os.makedirs(_DATA, exist_ok=True)
        open(os.path.join(_DATA, f"{name}_last"), "w").write(
            _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
    except Exception:  # noqa: BLE001
        pass
    return res


def _ev_llm_brief():
    ok, blocked = _cog_gate("llm_brief", _gate_hours(3, 2))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "write a natural-language market brief",
            "proposal": {"kind": "llm_brief"}}


def _ap_llm_brief(p):
    from . import cognition
    return _cog_apply("llm_brief", cognition.brief)


def _ev_llm_catalysts():
    ok, blocked = _cog_gate("llm_catalysts", _gate_hours(2, 1))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "extract tradable catalysts from news",
            "proposal": {"kind": "llm_catalysts"}}


def _ap_llm_catalysts(p):
    from . import cognition
    return _cog_apply("llm_catalysts", cognition.news_catalysts)


def _ev_llm_postmortem():
    ok, blocked = _cog_gate("llm_postmortem", _gate_hours(8, 10))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "review resolved decisions -> lessons",
            "proposal": {"kind": "llm_postmortem"}}


def _ap_llm_postmortem(p):
    from . import cognition
    return _cog_apply("llm_postmortem", cognition.postmortem)


def _ev_llm_risk():
    ok, blocked = _cog_gate("llm_risk", _gate_hours(3, 1.5))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "risk sentinel: scan for tail/concentration risk",
            "proposal": {"kind": "llm_risk"}}


def _ap_llm_risk(p):
    from . import cognition
    return _cog_apply("llm_risk", cognition.risk_scan)


def _ev_llm_adjudicate():
    ok, blocked = _cog_gate("llm_adjudicate", _gate_hours(4, 6))
    if not ok:
        return blocked
    try:
        from . import beliefs
        if not beliefs.conflicts():
            return {"eligible": False, "reason": "no belief conflicts to resolve"}
    except Exception:  # noqa: BLE001
        pass
    return {"eligible": True, "reason": "adjudicate conflicting beliefs",
            "proposal": {"kind": "llm_adjudicate"}}


def _ap_llm_adjudicate(p):
    from . import cognition
    return _cog_apply("llm_adjudicate", cognition.adjudicate)


# ---- free-model cognition, suite 2 (deeper reasoning jobs) ---- #
def _ev_llm_macro():
    ok, blocked = _cog_gate("llm_macro", _gate_hours(3, 2))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "cross-asset macro thesis",
            "proposal": {"kind": "llm_macro"}}


def _ap_llm_macro(p):
    from . import cognition2
    return _cog_apply("llm_macro", cognition2.macro_analysis)


def _ev_llm_second_opinion():
    ok, blocked = _cog_gate("llm_second_opinion", _gate_hours(2, 1.5))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "independent second opinion on the most split name",
            "proposal": {"kind": "llm_second_opinion"}}


def _ap_llm_second_opinion(p):
    from . import cognition2
    return _cog_apply("llm_second_opinion", cognition2.second_opinion)


def _ev_llm_theory():
    ok, blocked = _cog_gate("llm_theory", _gate_hours(6, 8))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "synthesize current operating theory (self-model)",
            "proposal": {"kind": "llm_theory"}}


def _ap_llm_theory(p):
    from . import cognition2
    return _cog_apply("llm_theory", cognition2.theory_synthesis)


def _ev_llm_watchlist_review():
    ok, blocked = _cog_gate("llm_watchlist_review", _gate_hours(3, 2))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "review armed watch->strike theses for staleness",
            "proposal": {"kind": "llm_watchlist_review"}}


def _ap_llm_watchlist_review(p):
    from . import cognition2
    return _cog_apply("llm_watchlist_review", cognition2.watchlist_review)


def _ev_llm_strategy_review():
    ok, blocked = _cog_gate("llm_strategy_review", _gate_hours(8, 10))
    if not ok:
        return blocked
    return {"eligible": True, "reason": "review learned weights + attribution -> tweaks",
            "proposal": {"kind": "llm_strategy_review"}}


def _ap_llm_strategy_review(p):
    from . import cognition2
    return _cog_apply("llm_strategy_review", cognition2.strategy_review)


def _ev_llm_anomaly():
    ok, blocked = _cog_gate("llm_anomaly", _gate_hours(2, 1))
    if not ok:
        return blocked
    try:
        from . import mesh_anomaly
        if not mesh_anomaly.summary().get("anomalies"):
            return {"eligible": False, "reason": "no anomalies to explain"}
    except Exception:  # noqa: BLE001
        pass
    return {"eligible": True, "reason": "explain live mesh anomalies",
            "proposal": {"kind": "llm_anomaly"}}


def _ap_llm_anomaly(p):
    from . import cognition2
    return _cog_apply("llm_anomaly", cognition2.anomaly_explain)


def _ev_relax_when_starved():
    """Break the selectivity ratchet. In stressed regimes several agents keep
    proposing to RAISE MIN_CONFIDENCE ("thin edge -> be pickier"), the governor
    auto-applies them, and nothing ever lowers it -- so the desk decides
    constantly but never trades, and with no trades the edge stays "thin," which
    triggers more raises. If the desk is deciding a lot but almost never trading,
    step the confidence floor back DOWN so the learning loop isn't starved.
    Paper-only; the policy engine, drawdown breaker and safety lock still gate
    every order below this."""
    try:
        from dashboard import dash_metrics
        sm = dash_metrics.summary()
    except Exception as e:  # noqa: BLE001
        return {"eligible": False, "reason": f"no bot summary ({str(e)[:40]})"}
    total = int(sm.get("total_decisions", 0))
    orders = int(sm.get("orders", 0))
    if total < 60:
        return {"eligible": False, "reason": f"not enough decisions yet ({total})"}
    rate = orders / max(1, total)
    floor = 0.50
    cur = float(_cur_param("MIN_CONFIDENCE", 0.60))
    if rate >= 0.03 or cur <= floor:
        return {"eligible": False, "reason": f"trading enough ({rate:.0%}) / at floor ({cur})"}
    last = _age_h(os.path.join(_DATA, "relax_starved_last"))
    if last is not None and last < 1.0:
        return {"eligible": False, "reason": f"relaxed recently ({last:.1f}h < 1h)"}
    step = 0.08 if (orders == 0 and total >= 100) else 0.04
    target = round(max(floor, cur - step), 3)
    return {"eligible": True,
            "reason": f"desk starved ({orders}/{total} orders) -> MIN_CONFIDENCE {cur}->{target}",
            "proposal": {"kind": "param", "name": "MIN_CONFIDENCE", "value": target}}


def _ap_relax_starved(p):
    import time as _t
    res = _ap_param(p)
    try:
        os.makedirs(_DATA, exist_ok=True)
        open(os.path.join(_DATA, "relax_starved_last"), "w").write(
            _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
    except Exception:  # noqa: BLE001
        pass
    return res


ACTIONS = {
    "relax_when_starved":   {"evaluate": _ev_relax_when_starved, "apply": _ap_relax_starved,
                             "auto_safe": True, "desc": "lower the confidence floor when the desk decides a lot but never trades (un-starve the learning loop)"},
    "llm_macro":            {"evaluate": _ev_llm_macro, "apply": _ap_llm_macro,
                             "auto_safe": True, "desc": "free-model cross-asset macro thesis"},
    "llm_second_opinion":   {"evaluate": _ev_llm_second_opinion, "apply": _ap_llm_second_opinion,
                             "auto_safe": True, "desc": "free-model independent second opinion on the most split council call"},
    "llm_theory":           {"evaluate": _ev_llm_theory, "apply": _ap_llm_theory,
                             "auto_safe": True, "desc": "free-model synthesis of the system's current operating theory"},
    "llm_watchlist_review": {"evaluate": _ev_llm_watchlist_review, "apply": _ap_llm_watchlist_review,
                             "auto_safe": True, "desc": "free-model staleness review of the armed watch->strike list"},
    "llm_strategy_review":  {"evaluate": _ev_llm_strategy_review, "apply": _ap_llm_strategy_review,
                             "auto_safe": True, "desc": "free-model review of learned weights + voice attribution"},
    "llm_anomaly":          {"evaluate": _ev_llm_anomaly, "apply": _ap_llm_anomaly,
                             "auto_safe": True, "desc": "free-model explanation of live mesh anomalies"},
    "llm_brief":            {"evaluate": _ev_llm_brief, "apply": _ap_llm_brief,
                             "auto_safe": True, "desc": "free-model market brief (regime+forecast+news+dream -> language)"},
    "llm_catalysts":        {"evaluate": _ev_llm_catalysts, "apply": _ap_llm_catalysts,
                             "auto_safe": True, "desc": "free-model news->structured catalysts -> arm watchlist + beliefs"},
    "llm_postmortem":       {"evaluate": _ev_llm_postmortem, "apply": _ap_llm_postmortem,
                             "auto_safe": True, "desc": "free-model review of resolved decisions -> durable lessons"},
    "llm_risk":             {"evaluate": _ev_llm_risk, "apply": _ap_llm_risk,
                             "auto_safe": True, "desc": "free-model risk sentinel (advisory tail/concentration warnings)"},
    "llm_adjudicate":       {"evaluate": _ev_llm_adjudicate, "apply": _ap_llm_adjudicate,
                             "auto_safe": True, "desc": "free-model adjudication of conflicting self-built beliefs"},
    "dream":                {"evaluate": _ev_dream, "apply": _ap_dream,
                             "auto_safe": True, "desc": "sleep-phase consolidation while the market is closed (replay, consolidate, counterfactual dream, study, retrain)"},
    "tune_aggression":      {"evaluate": _ev_tune_aggression, "apply": _ap_tune_aggression,
                             "auto_safe": True, "desc": "learn risk appetite from realized edge/drawdown"},
    "discover_strategy":    {"evaluate": _ev_discover_strategy, "apply": _ap_discover_strategy,
                             "auto_safe": True, "desc": "run hypothesis->backtest->promote sweep (self-teaching)"},
    "run_backtest":         {"evaluate": _ev_run_backtest, "apply": _ap_run_backtest,
                             "auto_safe": True, "desc": "run a walk-forward backtest vs SPY (Alpaca data)"},
    "prune_voice":          {"evaluate": _ev_prune_voice, "apply": _ap_prune_voice,
                             "auto_safe": True, "desc": "mute a proven-unprofitable voice (>=20 agreeing resolved decisions; reversible)"},
    "promote_voice":        {"evaluate": _ev_promote_voice, "apply": _ap_promote_voice,
                             "auto_safe": True, "desc": "pin a proven-profitable voice (>=20 agreeing resolved decisions; reversible)"},
    "tighten_selectivity":  {"evaluate": _ev_tighten_selectivity, "apply": _ap_param,
                             "auto_safe": True, "desc": "raise selectivity when confluence underperforms"},
    "risk_guard":           {"evaluate": _ev_risk_guard, "apply": _ap_param,
                             "auto_safe": True, "desc": "raise min-confidence in stressed regimes"},
    "enable_neural_core":   {"evaluate": _ev_enable_cortex, "apply": _ap_enable_cortex,
                             "auto_safe": False, "desc": "enable the neural core once it beats the linear blend"},
    "retrain_stale_ml":     {"evaluate": _ev_retrain_stale_ml, "apply": _ap_retrain_ml,
                             "auto_safe": True, "desc": "retrain the ML model when stale (champion-gated)"},
    "recalibrate_tnet":     {"evaluate": _ev_recalibrate_tnet, "apply": _ap_recalibrate,
                             "auto_safe": True, "desc": "recalibrate the transformer when stale"},
    "bootstrap_brain":      {"evaluate": _ev_bootstrap_brain, "apply": _ap_bootstrap_brain,
                             "auto_safe": True, "desc": "cold-start the fusion brain from historical decisions (one-shot)"},
    "train_cortex":         {"evaluate": _ev_train_cortex, "apply": _ap_train_cortex,
                             "auto_safe": True, "desc": "train the neural core when untrained/stale (champion-gated)"},
    "reflect":              {"evaluate": _ev_reflect, "apply": _ap_reflect,
                             "auto_safe": True, "desc": "reflect on internal state + build self-knowledge (beliefs)"},
    "scan_catalysts":       {"evaluate": _ev_scan_catalysts, "apply": _ap_scan_catalysts,
                             "auto_safe": True, "desc": "scan momentum catalysts + arm the watch/strike list"},
    "calibrate_meta":       {"evaluate": _ev_calibrate_meta, "apply": _ap_calibrate_meta,
                             "auto_safe": True, "desc": "calibrate probabilities + train the meta-labeler"},
    "prune_data_logs":      {"evaluate": _ev_prune_data_logs, "apply": _ap_prune_logs,
                             "auto_safe": True, "desc": "prune growth-prone logs to stay bounded"},
    "relax_selectivity":    {"evaluate": _ev_relax_selectivity, "apply": _ap_param,
                             "auto_safe": False, "desc": "loosen selectivity when confluence over-gates yet stays accurate"},
}


# ============================ sweep ====================================== #
def evaluate_all() -> list[dict]:
    """Pure read: status of every action (no mutation)."""
    out = []
    for name, a in ACTIONS.items():
        try:
            ev = a["evaluate"]()
        except Exception as e:  # noqa: BLE001
            ev = {"eligible": False, "reason": f"eval error: {str(e)[:80]}"}
        out.append({"action": name, "desc": a["desc"], "auto_safe": a["auto_safe"],
                    "eligible": bool(ev.get("eligible")), "reason": ev.get("reason", ""),
                    "proposal": ev.get("proposal")})
    return out


def sweep() -> dict:
    circuit_breaker_check()          # hard rail runs first, may halt autonomy
    p = policy()
    if p["kill_switch"] or p["mode"] == "off":
        return {"mode": p["mode"], "kill_switch": p["kill_switch"], "disabled": True, "results": []}
    results = []
    for name, a in ACTIONS.items():
        try:
            ev = a["evaluate"]()
        except Exception as e:  # noqa: BLE001
            results.append({"action": name, "status": "error", "reason": str(e)[:100]})
            continue
        if not ev.get("eligible"):
            results.append({"action": name, "status": "blocked", "reason": ev.get("reason", "")})
            continue
        if p["mode"] == "auto" and a["auto_safe"]:
            try:
                res = a["apply"](ev["proposal"])
                entry = {"action": name, "status": "applied", "reason": ev["reason"], "detail": res}
                try:
                    from . import mesh
                    mesh.publish("autonomy", "action", f"applied {name}: {ev['reason']}", salience=0.6)
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                entry = {"action": name, "status": "error", "reason": str(e)[:100]}
        else:
            entry = {"action": name, "status": "proposed", "reason": ev["reason"],
                     "proposal": ev.get("proposal")}
        _audit(entry)
        results.append(entry)
    return {"mode": p["mode"], "kill_switch": p["kill_switch"], "disabled": False, "results": results}


def status() -> dict:
    p = policy()
    return {"policy": p, "actions": evaluate_all(), "recent": recent_audit(12)}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print(json.dumps(status(), indent=2)[:1500])
