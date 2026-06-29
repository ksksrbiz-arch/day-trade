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
def policy() -> dict:
    try:
        with open(POLICY) as f:
            d = json.load(f)
        return {"mode": d.get("mode", "propose"), "kill_switch": bool(d.get("kill_switch", False)),
                "updated": d.get("updated", "")}
    except Exception:  # noqa: BLE001
        return {"mode": "propose", "kill_switch": False, "updated": ""}


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
    return {"eligible": True, "reason": f"ML model stale ({age:.0f}h) -> retrain (champion-gated)",
            "proposal": {"kind": "retrain_ml"}}


def _ap_retrain_ml(p):
    from .ml.train import train_once
    return train_once()


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


ACTIONS = {
    "prune_voice":          {"evaluate": _ev_prune_voice, "apply": _ap_prune_voice,
                             "auto_safe": False, "desc": "mute a proven-unprofitable voice"},
    "promote_voice":        {"evaluate": _ev_promote_voice, "apply": _ap_promote_voice,
                             "auto_safe": False, "desc": "pin a proven-profitable voice"},
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
    "train_cortex":         {"evaluate": _ev_train_cortex, "apply": _ap_train_cortex,
                             "auto_safe": True, "desc": "train the neural core when untrained/stale (champion-gated)"},
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
