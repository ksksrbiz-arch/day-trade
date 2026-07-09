"""
Bot manager: launch and supervise multiple concurrent PAPER trading bots.

Each bot is a detached `python -m trader.run` subprocess with its own env
overrides (size, gates, shorting, universe, data sources) and its OWN ledger at
data/bots/<id>/trades.csv. The registry persists to data/bots.json so bots
survive a dashboard restart (the OS processes keep running regardless).

All bots trade Alpaca PAPER. There is intentionally no real-money path here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DATA = PROJ / "data"
BOTS_DIR = DATA / "bots"
REGISTRY = DATA / "bots.json"

DEFAULT_PARAMS = {
    "notional": 1000.0,
    "min_confidence": 0.45,
    "min_sentiment": 0.25,
    "allow_short": True,
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
    "universe": "",            # comma list; "" = any ticker
    "use_confirmation": True,
    "use_clearstreet": False,
    "dynamic_sizing": True,
    "adaptive_exits": True,
    "regime_filter": True,
    "cooldown_min": 30.0,
    "min_rr": 2.0,
    "daily_max_dd": 3.0,
    "mode": "daytrader",   # watch->wait->strike; acts on scanner+factor armed setups
    "scalper_universe": "",
    "scalper_window": 20,
    "scalper_k": 2.0,
    "use_ofi": False,
    "ofi_threshold": 0.6,
    "use_options": False,
    "extended_hours": False,
    "use_omni": False,
    "omni_gate": False,
    "watch_buffer": 0.005,
    "watch_expiry_min": 180,
    "poll_seconds": 300,
}


def _load() -> dict:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text())
        except Exception:
            return {}
    return {}


def _save(reg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        import psutil  # optional
        return psutil.pid_exists(int(pid))
    except Exception:
        pass
    # POSIX (Linux/Render): signal 0 probes existence without touching the process
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                     # exists, just not ours to signal
        except Exception:
            return False
    # fallback: tasklist on Windows
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}"],
                             capture_output=True, text=True, timeout=8)
        return str(pid) in out.stdout
    except Exception:
        return False


def _real_pid(bot) -> int | None:
    pf = BOTS_DIR / bot["id"] / "pid"
    if pf.exists():
        try:
            return int(pf.read_text().strip())
        except Exception:
            return None
    return None


def _bot_alive(bot) -> bool:
    return _alive(_real_pid(bot)) or _alive(bot.get("pid"))


def list_bots() -> list[dict]:
    reg = _load()
    changed = False
    for b in reg.values():
        running = _bot_alive(b)
        new_status = "running" if running else ("stopped" if b.get("status") != "created" else "created")
        if b.get("status") == "running" and not running:
            new_status = "stopped"
        if b.get("status") != new_status and not (b.get("status") == "created" and not running):
            b["status"] = new_status
            changed = True
    if changed:
        _save(reg)
    return list(reg.values())


def get_bot(bot_id: str) -> dict | None:
    return _load().get(bot_id)


def create_bot(name: str, params: dict | None = None) -> dict:
    reg = _load()
    bot_id = uuid.uuid4().hex[:8]
    p = dict(DEFAULT_PARAMS)
    if params:
        for k in DEFAULT_PARAMS:
            if k in params and params[k] is not None:
                p[k] = params[k]
    bot = {
        "id": bot_id,
        "name": name or f"bot-{bot_id}",
        "params": p,
        "status": "created",
        "pid": None,
        "created": time.time(),
        "ledger": str((BOTS_DIR / bot_id / "trades.csv").relative_to(PROJ)),
    }
    reg[bot_id] = bot
    _save(reg)
    return bot


def _bot_env(bot: dict) -> dict:
    p = bot["params"]
    env = dict(os.environ)
    d = BOTS_DIR / bot["id"]
    d.mkdir(parents=True, exist_ok=True)
    env.update({
        "NOTIONAL_PER_TRADE": str(p["notional"]),
        "MIN_CONFIDENCE": str(p["min_confidence"]),
        "MIN_SENTIMENT": str(p["min_sentiment"]),
        "ALLOW_SHORT": "true" if p["allow_short"] else "false",
        "TAKE_PROFIT_PCT": str(p["take_profit_pct"]),
        "STOP_LOSS_PCT": str(p["stop_loss_pct"]),
        "UNIVERSE": p["universe"] or "",
        "USE_CONFIRMATION": "true" if p["use_confirmation"] else "false",
        "USE_CLEARSTREET": "true" if p["use_clearstreet"] else "false",
        "DYNAMIC_SIZING": "true" if p.get("dynamic_sizing") else "false",
        "ADAPTIVE_EXITS": "true" if p.get("adaptive_exits") else "false",
        "REGIME_FILTER": "true" if p.get("regime_filter") else "false",
        "COOLDOWN_MIN": str(p.get("cooldown_min", 0)),
        "MIN_RR": str(p.get("min_rr", 2.0)),
        "DAILY_MAX_DD": str(p.get("daily_max_dd", 3.0)),
        "MODE": p.get("mode", "news"),
        "SCALPER_UNIVERSE": p.get("scalper_universe", "") or "",
        "SCALPER_WINDOW": str(p.get("scalper_window", 20)),
        "SCALPER_K": str(p.get("scalper_k", 2.0)),
        "USE_OFI": "true" if p.get("use_ofi") else "false",
        "OFI_THRESHOLD": str(p.get("ofi_threshold", 0.6)),
        "USE_OPTIONS": "true" if p.get("use_options") else "false",
        "EXTENDED_HOURS": "true" if p.get("extended_hours") else "false",
        "USE_OMNI": "true" if p.get("use_omni") else "false",
        "OMNI_GATE": "true" if p.get("omni_gate") else "false",
        "WATCH_BUFFER": str(p.get("watch_buffer", 0.005)),
        "WATCH_EXPIRY_MIN": str(p.get("watch_expiry_min", 180)),
        "POLL_SECONDS": str(p["poll_seconds"]),
        "TRADE_LOG": f"data/bots/{bot['id']}/trades.csv",
        "SEEN_PATH": f"data/bots/{bot['id']}/seen.json",
        "PYTHONUNBUFFERED": "1",
        "BOT_PID_FILE": f"data/bots/{bot['id']}/pid",
        "BOT_ID": bot["id"],
    })
    return env


def start_bot(bot_id: str) -> dict | None:
    reg = _load()
    bot = reg.get(bot_id)
    if not bot:
        return None
    if _alive(bot.get("pid")):
        return bot
    d = BOTS_DIR / bot_id
    d.mkdir(parents=True, exist_ok=True)
    log = open(d / "run.log", "a", buffering=1)
    cre=0
    if os.name == "nt":
        cre=subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "trader.run"],
        cwd=str(PROJ), env=_bot_env(bot),
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=cre if os.name == "nt" else 0,
    )
    bot["pid"] = proc.pid
    bot["enabled"] = True
    bot["status"] = "running"
    bot["started"] = time.time()
    _save(reg)
    return bot


def stop_bot(bot_id: str) -> dict | None:
    reg = _load()
    bot = reg.get(bot_id)
    if not bot:
        return None
    rp = _real_pid(bot)
    if rp:
        try:
            subprocess.run(["taskkill", "/PID", str(rp), "/T", "/F"], capture_output=True, timeout=10)
        except Exception:
            pass
    try:
        (BOTS_DIR / bot["id"] / "pid").unlink(missing_ok=True)
    except Exception:
        pass
    pid = bot.get("pid")
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                os.kill(int(pid), 9)
        except Exception:
            pass
    bot["status"] = "stopped"
    bot["pid"] = None
    bot["enabled"] = False
    _save(reg)
    return bot


def delete_bot(bot_id: str) -> bool:
    stop_bot(bot_id)
    reg = _load()
    if bot_id in reg:
        del reg[bot_id]
        _save(reg)
        return True
    return False
