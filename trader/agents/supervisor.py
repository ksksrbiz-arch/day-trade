"""Self-repair supervisor (watchdog).

Monitors the long-running daemons and the dashboard, and RESTARTS any that have
died -- so the whole system heals itself without human intervention. Health is
written to the durable kv store and surfaced on the dashboard.

  one health check:   python -m trader.agents.supervisor
  continuous:         python -m trader.agents.supervisor --loop --every 120
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

import os
import subprocess
import sys
import time

from . import state

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.abspath(os.path.join(_HERE, "..", ".."))
_PY = os.path.join(_PROJ, ".venv", "Scripts", "python.exe")
if not os.path.exists(_PY):
    _PY = sys.executable

# name -> (match substring in command line, launch args, logfile, health_url|None)
# health_url, when set, is an HTTP liveness probe: a service that is *running* but
# not responding (hung) is treated as down and force-restarted.
SERVICES = {
    "dashboard":  ("dashboard.app:app",
                   ["-m", "uvicorn", "dashboard.app:app", "--host", "127.0.0.1", "--port", "8000"],
                   "dash", "http://127.0.0.1:8000/api/health/full"),
    "exits":      ("trader.exits", ["-m", "trader.exits"], "exits", None),
    "optimizer":  ("dashboard.optimizer", ["-m", "dashboard.optimizer", "--daemon"], "opt", None),
    "autotuner":  ("dashboard.autotuner", ["-m", "dashboard.autotuner"], "at", None),
    "ml_daemon":  ("trader.ml.daemon", ["-m", "trader.ml.daemon", "--every", "6"], "ml", None),
    "agents":     ("trader.agents.runtime", ["-m", "trader.agents.runtime", "--loop", "--every", "900"], "agents", None),
}

_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _running_cmdlines() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=25)
        return out.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def _launch(name: str, args: list[str], log: str):
    os.makedirs(os.path.join(_PROJ, "data"), exist_ok=True)
    out = open(os.path.join(_PROJ, "data", f"{log}.out.log"), "a")
    err = open(os.path.join(_PROJ, "data", f"{log}.err.log"), "a")
    subprocess.Popen([_PY] + args, cwd=_PROJ, stdout=out, stderr=err,
                     creationflags=_DETACHED, close_fds=True)


def _http_ok(url: str, timeout: float = 4.0) -> bool:
    """True if the URL responds at all (any non-5xx) -- a liveness, not correctness, check."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 500
    except Exception:  # noqa: BLE001
        return False


def _port_open(port: int, timeout: float = 1.0) -> bool:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _kill(match: str):
    """Force-kill any python process whose command line matches (a hung service)."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             f"Where-Object {{ $_.CommandLine -match [regex]::Escape('{match}') }} | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=20)
        time.sleep(1.5)
    except Exception:  # noqa: BLE001
        pass


def _launch_brain():
    """Best-effort launch of the Next.js brain dev server (port 3000)."""
    brain = os.path.join(_PROJ, "brain")
    if not os.path.isdir(brain):
        return
    out = open(os.path.join(_PROJ, "data", "brain.out.log"), "a")
    err = open(os.path.join(_PROJ, "data", "brain.err.log"), "a")
    try:
        subprocess.Popen("npm run dev", cwd=brain, stdout=out, stderr=err,
                         shell=True, creationflags=_DETACHED, close_fds=True)
    except Exception:  # noqa: BLE001
        pass


_RESTART_WINDOW = 600       # seconds
_CRASH_MAX = 3              # restarts within window before backing off


def _record_restart(name: str):
    ts = state.kv_get("supervisor_restart_ts", {}) or {}
    arr = [t for t in ts.get(name, []) if time.time() - t < _RESTART_WINDOW]
    arr.append(time.time())
    ts[name] = arr
    state.kv_set("supervisor_restart_ts", ts)


def _crash_looping(name: str) -> bool:
    ts = state.kv_get("supervisor_restart_ts", {}) or {}
    arr = [t for t in ts.get(name, []) if time.time() - t < _RESTART_WINDOW]
    return len(arr) >= _CRASH_MAX


def selftest() -> dict:
    """Lightweight platform self-test: core imports, key dirs, dashboard liveness.
    Publishes failures to the mesh and stores the result for the dashboard."""
    checks = {}
    for m in ("trader.alpha", "trader.tnet", "trader.cortex", "trader.autonomy",
              "trader.newshub", "trader.timeline", "trader.shadow", "trader.mesh"):
        try:
            __import__(m)
            checks[m] = "ok"
        except Exception as e:  # noqa: BLE001
            checks[m] = f"FAIL {str(e)[:60]}"
    for d in ("data", os.path.join("data", "mesh"), os.path.join("data", "agents")):
        checks["dir:" + d] = "ok" if os.path.isdir(os.path.join(_PROJ, d)) else "missing"
    checks["dashboard"] = "ok" if _http_ok("http://127.0.0.1:8000/api/health/full") else "down"
    fails = [k for k, v in checks.items() if v != "ok"]
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "ok": not fails, "fails": fails, "checks": checks}
    try:
        state.kv_set("supervisor_selftest", res)
        if fails:
            from .. import mesh
            mesh.publish("supervisor", "selftest", "selftest FAIL: " + ", ".join(fails), salience=0.75)
    except Exception:  # noqa: BLE001
        pass
    return res


def check_and_heal(heal: bool = True) -> dict:
    cmd = _running_cmdlines()
    status, restarts, crashloops = {}, [], []
    counts = state.kv_get("supervisor_restarts", {}) or {}
    for name, (match, args, log, health_url) in SERVICES.items():
        present = match in cmd
        healthy = present and (_http_ok(health_url) if health_url else True)
        status[name] = healthy
        if not healthy and heal:
            if _crash_looping(name):          # too many recent restarts -> back off, alert
                status[name] = "crash_loop"
                crashloops.append(name)
                continue
            if present and health_url:        # running but unresponsive (hung) -> clear it
                _kill(match)
            _launch(name, args, log)
            counts[name] = counts.get(name, 0) + 1
            _record_restart(name)
            restarts.append(name)
    # opt-in: keep the Next.js brain dev server (port 3000) alive
    if os.environ.get("WATCH_BRAIN", "").strip().lower() in ("1", "true", "yes", "on"):
        bok = _port_open(3000)
        status["brain"] = bok
        if not bok and heal:
            _launch_brain()
            counts["brain"] = counts.get("brain", 0) + 1
            restarts.append("brain")
    if crashloops:
        try:
            from .. import mesh
            mesh.publish("supervisor", "crash_loop",
                         f"crash-loop backoff: {', '.join(crashloops)} "
                         f"restarted >={_CRASH_MAX}x in {_RESTART_WINDOW // 60}m — backing off",
                         salience=0.85)
        except Exception:  # noqa: BLE001
            pass
    st = selftest()
    health = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "services": status, "restarted": restarts, "crash_loops": crashloops,
              "restart_counts": counts, "selftest": {"ok": st["ok"], "fails": st["fails"]}}
    try:
        state.kv_set("system_health", health)
        state.kv_set("supervisor_restarts", counts)
    except Exception:  # noqa: BLE001
        pass
    return health


def main():
    loop = "--loop" in sys.argv
    every = 120
    heal = "--no-heal" not in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--every" and i + 1 < len(sys.argv):
            every = int(sys.argv[i + 1])
    print(f"[supervisor] watching {list(SERVICES)} loop={loop} heal={heal}")
    while True:
        h = check_and_heal(heal=heal)
        down = [k for k, v in h["services"].items() if not v]
        print(f"[supervisor] up={sum(h['services'].values())}/{len(h['services'])} "
              f"down={down} restarted={h['restarted']}")
        if not loop:
            break
        time.sleep(every)


if __name__ == "__main__":
    main()
