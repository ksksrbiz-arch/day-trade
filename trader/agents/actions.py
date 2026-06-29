"""Heavier agent actions: files, sandboxed code execution, parallel subagents,
and context offload. All bounded and fail-soft; mutating ones are governed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import cloudflare as cf, governor

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.abspath(os.path.join(_HERE, "..", ".."))
WORKSPACE = os.path.join(_PROJ, "data", "agents", "workspace")


def _safe_under(base: str, path: str) -> str | None:
    full = os.path.abspath(os.path.join(base, path))
    return full if full.startswith(os.path.abspath(base)) else None


# ---- files ---------------------------------------------------------------- #
def file_read(path: str = "", **_):
    """Read a project file (read-only). Path is relative to the project root."""
    full = _safe_under(_PROJ, path)
    if not full or not os.path.isfile(full):
        return {"error": f"not readable: {path}"}
    try:
        return {"path": path, "text": open(full, encoding="utf-8", errors="replace").read()[:6000]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def file_write(agent: str = "system", path: str = "", content: str = "", **_):
    """Write a file INSIDE the agent workspace sandbox only."""
    os.makedirs(WORKSPACE, exist_ok=True)
    full = _safe_under(WORKSPACE, path)
    if not full:
        return {"error": "writes are restricted to the agent workspace"}
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w", encoding="utf-8").write(str(content)[:50000])
        governor.record_action(agent, "file_write", f"wrote {path}", {"bytes": len(content)})
        return {"ok": True, "path": os.path.relpath(full, _PROJ)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


# ---- sandboxed code execution --------------------------------------------- #
def run_python(agent: str = "system", code: str = "", timeout: int = 20, **_):
    """Execute Python in a SEPARATE process with a timeout. Output captured.
    Intended for analysis snippets; no persistence beyond the workspace."""
    if not code.strip():
        return {"error": "empty code"}
    os.makedirs(WORKSPACE, exist_ok=True)
    py = os.path.join(_PROJ, ".venv", "Scripts", "python.exe")
    if not os.path.exists(py):
        py = sys.executable
    try:
        p = subprocess.run([py, "-c", code], cwd=_PROJ, capture_output=True,
                           text=True, timeout=timeout)
        out = (p.stdout or "")[-3000:]
        err = (p.stderr or "")[-1000:]
        governor.record_action(agent, "run_python",
                               f"exec rc={p.returncode}", {"rc": p.returncode})
        return {"returncode": p.returncode, "stdout": out, "stderr": err}
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


# ---- context offload ------------------------------------------------------ #
def summarize_offload(agent: str = "system", text: str = "", label: str = "result", **_):
    """Summarize a large blob and offload the full text to a workspace file,
    returning a compact summary + reference -- keeps working context small."""
    if not text:
        return {"error": "no text"}
    os.makedirs(WORKSPACE, exist_ok=True)
    fn = f"{label}_{int(time.time())}.txt"
    full = os.path.join(WORKSPACE, fn)
    try:
        open(full, "w", encoding="utf-8").write(str(text)[:200000])
    except Exception:  # noqa: BLE001
        pass
    summary = cf.summarize(str(text)) if cf.available() else str(text)[:300]
    return {"summary": summary, "offloaded_to": os.path.relpath(full, _PROJ),
            "original_chars": len(str(text))}


# ---- parallel subagents (isolated context) -------------------------------- #
_READ_TOOLS_DESC = {
    "brain_state": "current regime + posture",
    "ml_card": "ML model metrics",
    "confluence": "conviction for a symbol (arg symbol)",
    "latest_backtest": "last backtest vs SPY",
    "news_sentiment": "Cloudflare sentiment for a symbol (arg symbol)",
    "file_read": "read a project file (arg path)",
}


def _subagent(role: str, task: str) -> dict:
    """One focused agent in an ISOLATED context: pick ONE read tool, run it,
    return a compact finding. No shared blackboard."""
    from . import tools
    cat = "\n".join(f"- {k}: {v}" for k, v in _READ_TOOLS_DESC.items())
    prompt = (f"You are a {role} subagent. Task: {task}\n"
              f"Pick ONE tool to gather evidence:\n{cat}\n"
              'Reply STRICT JSON only: {"tool":"<name>","args":{...},"finding":"<one sentence>"}')
    raw = cf.chat(prompt, max_tokens=200) if cf.available() else ""
    import re
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    act = {}
    if m:
        try:
            act = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            act = {}
    tool = act.get("tool")
    result = tools.call(tool, agent=f"sub:{role}", **(act.get("args") or {})) if tool in tools.REGISTRY else {"note": "no tool"}
    return {"role": role, "task": task, "finding": act.get("finding", raw[:120]),
            "tool": tool, "result": result}


def spawn_subagents(tasks: list[dict], max_workers: int = 4) -> list[dict]:
    """Run focused subagents in parallel, isolated context windows.
    tasks: [{role, task}]. Returns their findings."""
    out = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_subagent, t.get("role", "analyst"), t.get("task", "")): t
                for t in tasks}
        for f in as_completed(futs):
            try:
                out.append(f.result())
            except Exception as e:  # noqa: BLE001
                out.append({"error": str(e)[:120]})
    return out


def t_spawn_subagents(agent: str = "system", tasks=None, **_):
    tasks = tasks or [{"role": "macro", "task": "is the regime risk-on or risk-off?"},
                      {"role": "quant", "task": "is the latest backtest edge positive?"}]
    res = spawn_subagents(tasks)
    governor.record_action(agent, "spawn_subagents", f"{len(res)} subagents reported",
                           {"n": len(res)})
    return {"subagents": res}


if __name__ == "__main__":
    print("file_read:", file_read(path="README.md").get("path", file_read(path="trader/agents/__init__.py").get("path")))
    print("run_python:", run_python(code="print(2+2)"))
    print("subagents:", json.dumps(spawn_subagents(
        [{"role": "macro", "task": "regime?"}]), default=str)[:200])
