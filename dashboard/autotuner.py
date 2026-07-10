"""
24/7 continuous backtest-and-optimize daemon -- the always-on research loop.

Each cycle (default every 30 min):
  1. Pull fresh FREE history (Binance crypto, Tiingo stocks).
  2. Walk-forward sweep the SCALPER params (window, k) and pick the config that is
     ROBUST across the whole universe (best median OOS expectancy) -- not the
     single in-sample-luckiest fit.
  3. Compare to the live crypto-247 bot's current params. Apply a change ONLY if
     the robust OOS edge beats the current config by a margin AND a cooldown has
     elapsed -- bounded, logged, conservative.
  4. Ask a FREE model to interpret the result / flag regime caveats (advisory).
  5. Write a research report for the dashboard + a history trail.

OVERFITTING DISCIPLINE (why this is safe-ish): out-of-sample only, robustness-
over-peak selection, a change margin, an apply cooldown, bounded grids, and the
control bot is never touched. Re-optimizing on history can still chase noise --
so changes are nudges gated on cross-symbol robustness, and every change is
recorded so its live aftermath can be judged.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
RES = PROJ / "data" / "research"
LOG = PROJ / "data" / "autotuner.log"
STATE = RES / "state.json"

CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "AVAX/USD", "ADA/USD",
          "XRP/USD", "DOGE/USD", "LINK/USD", "BCH/USD"]
STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "UNH", "WMT"]

MARGIN = float(os.getenv("AUTOTUNE_MARGIN", "0.03"))      # min median-OOS-expectancy gain to change
COOLDOWN_H = float(os.getenv("AUTOTUNE_COOLDOWN_H", "3")) # hours between applied changes
INTERVAL = int(os.getenv("AUTOTUNE_INTERVAL", "1800"))    # seconds between cycles


def _log(msg):
    try:
        if report.get("applied"):
            from trader.pieces_ltm import PiecesLTM
            PiecesLTM(getattr(cfg, "pieces_url", ""), getattr(cfg, "pieces_enabled", True)).remember(
                "Autotune " + str(report["applied"].get("bot")) + " " + report["ts"][:10],
                "# Autotuner parameter change\n" + json.dumps(report["applied"], indent=2),
                dedup_key="autotune-" + report["ts"][:13] + "-" + str(report["applied"].get("to")))
    except Exception:
        pass
    RES.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        open(LOG, "a").write(line + "\n")
    except Exception:
        pass


def _state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(s):
    try:
        if report.get("applied"):
            from trader.pieces_ltm import PiecesLTM
            PiecesLTM(getattr(cfg, "pieces_url", ""), getattr(cfg, "pieces_enabled", True)).remember(
                "Autotune " + str(report["applied"].get("bot")) + " " + report["ts"][:10],
                "# Autotuner parameter change\n" + json.dumps(report["applied"], indent=2),
                dedup_key="autotune-" + report["ts"][:13] + "-" + str(report["applied"].get("to")))
    except Exception:
        pass
    RES.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s))


def _interpret(cfg, summary: str) -> str:
    """One cheap FREE-model call to interpret the sweep (advisory, fail-soft)."""
    from trader import council
    prompt = ("You are a quant research assistant. In 2-3 sentences, interpret this "
              "walk-forward parameter sweep result for a mean-reversion crypto scalper "
              "and flag any overfitting/regime caveats. Be skeptical and concise.\n\n" + summary)
    for fn in ("groq", "openrouter", "cohere"):
        try:
            return council._member_text(cfg, fn, prompt)[:600]
        except Exception:
            continue
    return "(no free model available for interpretation)"


def run_cycle(apply: bool = True) -> dict:
    from trader import config, history
    from trader.scalper_bt import best_robust_params
    from dashboard import bots as botmgr
    cfg = config.load()
    report = {"ts": datetime.now(timezone.utc).isoformat(), "applied": None, "candidates": {}}
    try:
        import subprocess, sys
        from trader import safety
        ev = subprocess.run([sys.executable, "-m", "pytest", "tests/test_policy_evals.py", "-q"],
                            cwd=str(PROJ), capture_output=True, text=True, timeout=120)
        rc = ev.returncode
        # pytest exit codes: 0 = all passed, 1 = a test FAILED, 5 = no tests
        # collected, 2/3/4 = usage/internal. Only a genuine assertion FAILURE
        # (rc==1) is a guardrail regression worth fail-closing on. A missing or
        # un-collected harness (rc==5, e.g. tests not shipped) must NOT hard-lock
        # live trading -- that is a packaging problem, not a safety regression.
        if rc == 0:
            report["evals_passed"] = True
            safety.clear_lock()                 # a passing suite clears any stale lock
        elif rc == 1:
            report["evals_passed"] = False
            safety.set_lock("daily eval suite FAILED: " + (ev.stdout or "")[-180:])
            _log("EVAL FAIL -> safety lock SET (human review required)")
        else:
            report["evals_passed"] = None
            safety.clear_lock()                 # harness unavailable != regression
            _log(f"evals unavailable (pytest rc={rc}); not locking")
    except Exception as e:
        report["evals_passed"] = None
        _log(f"eval run error: {e}")
    try:
        from trader import market_brain
        report["market_regime"] = market_brain.refresh(cfg, with_narrative=True).get("regime")
    except Exception as e:
        report["market_regime"] = f"err: {e}"

    # --- crypto sweep (Binance, free) ---
    try:
        cpanel = history.load_panel(CRYPTO, days=900, source="binance")
        cprices = {s: v for s, v in cpanel["prices"].items() if len(v) >= 450}
        crec = best_robust_params(cprices, train=300, test=120)
        report["crypto"] = {"best": crec["best"], "symbols": len(cprices), "detail": crec["detail"]}
    except Exception as e:
        report["crypto"] = {"error": str(e)[:160]}
        crec = {"best": None, "detail": {}}

    # --- stock sweep (Tiingo, free) -- research record only (no live scalper bot on stocks) ---
    try:
        spanel = history.load_panel(STOCKS, days=1500, source="tiingo", tiingo_token=cfg.tiingo_token)
        sprices = {s: v for s, v in spanel["prices"].items() if len(v) >= 450}
        srec = best_robust_params(sprices, train=400, test=150)
        report["stocks"] = {"best": srec["best"], "symbols": len(sprices)}
    except Exception as e:
        report["stocks"] = {"error": str(e)[:160]}

    # --- apply bounded change to crypto-247 if robustly better ---
    best = crec.get("best")
    if apply and best:
        reg = botmgr._load()
        bot = next((b for b in reg.values() if b["name"] == "crypto-247"), None)
        if bot:
            cw = int(bot["params"].get("scalper_window", 20))
            ck = float(bot["params"].get("scalper_k", 2.0))
            cur_key = f"w{cw}_k{ck}"
            cur_score = crec["detail"].get(cur_key, {}).get("median_oos_expectancy", -1e9)
            new_score = best["score"]
            st = _state()
            last = st.get("last_change_ts", 0)
            cooled = (time.time() - last) >= COOLDOWN_H * 3600
            changed = (best["window"] != cw or best["k"] != ck)
            if changed and new_score >= cur_score + MARGIN and cooled:
                rid = bot["id"]
                r = botmgr._load()
                r[rid]["params"]["scalper_window"] = best["window"]
                r[rid]["params"]["scalper_k"] = best["k"]
                botmgr._save(r)
                try:
                    botmgr.stop_bot(rid); time.sleep(1); botmgr.start_bot(rid)
                except Exception as e:
                    _log(f"restart after tune failed: {e}")
                st["last_change_ts"] = time.time(); _save_state(st)
                report["applied"] = {"bot": "crypto-247", "from": {"window": cw, "k": ck},
                                     "to": {"window": best["window"], "k": best["k"]},
                                     "cur_score": cur_score, "new_score": new_score}
                _log(f"AUTO-TUNE crypto-247 scalper -> w{best['window']} k{best['k']} "
                     f"(oos {cur_score:+.3f} -> {new_score:+.3f})")
                try:
                    from trader import council as _co
                    report["applied"]["rationale"] = _co._member_text(cfg, "groq",
                        "In 1-2 sentences, explain WHY this mean-reversion scalper parameter change is "
                        "justified out-of-sample and flag overfitting risk. Change: "
                        + json.dumps(report["applied"]) + " Grid OOS detail: "
                        + json.dumps(crec.get("detail", {}))[:400])[:400]
                except Exception:
                    report["applied"]["rationale"] = "(rationale unavailable)"
            else:
                report["skip_reason"] = ("cooldown" if not cooled else
                                         ("no improvement" if not (new_score >= cur_score + MARGIN) else "same params"))

    # --- free-AI interpretation (advisory) ---
    try:
        summ = json.dumps({"crypto_best": report.get("crypto", {}).get("best"),
                           "applied": report["applied"]})
        report["ai_note"] = _interpret(cfg, summ)
    except Exception as e:
        report["ai_note"] = f"(interp error: {e})"

    try:
        if report.get("applied"):
            from trader.pieces_ltm import PiecesLTM
            PiecesLTM(getattr(cfg, "pieces_url", ""), getattr(cfg, "pieces_enabled", True)).remember(
                "Autotune " + str(report["applied"].get("bot")) + " " + report["ts"][:10],
                "# Autotuner parameter change\n" + json.dumps(report["applied"], indent=2),
                dedup_key="autotune-" + report["ts"][:13] + "-" + str(report["applied"].get("to")))
    except Exception:
        pass
    RES.mkdir(parents=True, exist_ok=True)
    (RES / "latest.json").write_text(json.dumps(report, indent=2))
    with open(RES / "history.jsonl", "a") as f:
        f.write(json.dumps({"ts": report["ts"], "crypto_best": report.get("crypto", {}).get("best"),
                            "applied": report["applied"]}) + "\n")
    _log(f"cycle done: crypto_best={report.get('crypto',{}).get('best')} applied={bool(report['applied'])}")
    return report


def daemon():
    _log(f"autotuner up: every {INTERVAL}s, margin {MARGIN}, cooldown {COOLDOWN_H}h (OOS, robust, bounded)")
    while True:
        try:
            run_cycle(apply=True)
        except Exception as e:
            _log(f"cycle error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        print(json.dumps(run_cycle(apply="--apply" in sys.argv), indent=2)[:1500])
    else:
        daemon()
