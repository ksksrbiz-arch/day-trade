"""Durable state store for the agent runtime (SQLite).

This is what makes the agents stateful, durable, and self-repairing:

  runs        -- one per autonomy cycle; status + resume cursor
  steps       -- per-step status (pending/running/done/failed); enables EXACT
                 resume: completed steps are skipped when a crashed run restarts
  checkpoints -- serialized working state (blackboard) snapshot per run
  traces      -- full observability: every step's input/output/duration/status
  approvals   -- human-in-the-loop queue (pending/approved/rejected)
  kv          -- long-term persistent memory (key/value across sessions)

Pure stdlib sqlite3 -> no fragile deps for a 24/7 daemon.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "agents"))
DB = os.environ.get("AGENT_STATE_DB", os.path.join(_DATA, "state.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, status TEXT, cursor INTEGER DEFAULT 0,
    started TEXT, updated TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    run_id INTEGER, idx INTEGER, name TEXT, status TEXT,
    attempts INTEGER DEFAULT 0, result TEXT, error TEXT, updated TEXT,
    PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id INTEGER PRIMARY KEY, blob TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, run_id INTEGER, agent TEXT, step TEXT, tool TEXT,
    status TEXT, ms INTEGER, summary TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, agent TEXT, action TEXT, payload TEXT,
    status TEXT DEFAULT 'pending', resolved_ts TEXT, decided_by TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY, v TEXT, updated TEXT
);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def conn():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


# ---- runs / steps (durable execution) -------------------------------------- #
def start_run(kind: str, step_names: list[str], note: str = "") -> int:
    c = conn()
    cur = c.execute("INSERT INTO runs(kind,status,cursor,started,updated,note) "
                    "VALUES(?,?,?,?,?,?)", (kind, "running", 0, _now(), _now(), note))
    rid = cur.lastrowid
    for i, nm in enumerate(step_names):
        c.execute("INSERT OR REPLACE INTO steps(run_id,idx,name,status,updated) "
                  "VALUES(?,?,?,?,?)", (rid, i, nm, "pending", _now()))
    c.commit(); c.close()
    return rid


def resumable_run(kind: str):
    """Return (run_id, cursor) of the most recent unfinished run of this kind, else None."""
    c = conn()
    r = c.execute("SELECT id,cursor FROM runs WHERE kind=? AND status='running' "
                  "ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    c.close()
    return (r["id"], r["cursor"]) if r else None


def step_status(run_id: int, idx: int) -> str | None:
    c = conn()
    r = c.execute("SELECT status FROM steps WHERE run_id=? AND idx=?",
                  (run_id, idx)).fetchone()
    c.close()
    return r["status"] if r else None


def mark_step(run_id: int, idx: int, status: str, result=None, error: str = ""):
    c = conn()
    c.execute("UPDATE steps SET status=?, attempts=attempts+1, result=?, error=?, updated=? "
              "WHERE run_id=? AND idx=?",
              (status, json.dumps(result)[:4000] if result is not None else None,
               error[:500], _now(), run_id, idx))
    if status == "done":
        c.execute("UPDATE runs SET cursor=?, updated=? WHERE id=?", (idx + 1, _now(), run_id))
    c.commit(); c.close()


def finish_run(run_id: int, status: str = "done"):
    c = conn()
    c.execute("UPDATE runs SET status=?, updated=? WHERE id=?", (status, _now(), run_id))
    c.commit(); c.close()


# ---- checkpoints (working state) ------------------------------------------- #
def save_checkpoint(run_id: int, blackboard: dict):
    c = conn()
    c.execute("INSERT OR REPLACE INTO checkpoints(run_id,blob,updated) VALUES(?,?,?)",
              (run_id, json.dumps(blackboard)[:200000], _now()))
    c.commit(); c.close()


def load_checkpoint(run_id: int) -> dict:
    c = conn()
    r = c.execute("SELECT blob FROM checkpoints WHERE run_id=?", (run_id,)).fetchone()
    c.close()
    return json.loads(r["blob"]) if r and r["blob"] else {}


# ---- traces (observability) ------------------------------------------------ #
def trace(run_id, agent, step, tool, status, ms, summary, data=None):
    c = conn()
    c.execute("INSERT INTO traces(ts,run_id,agent,step,tool,status,ms,summary,data) "
              "VALUES(?,?,?,?,?,?,?,?,?)",
              (_now(), run_id, agent, step, tool, status, int(ms),
               (summary or "")[:300], json.dumps(data)[:2000] if data else None))
    c.commit(); c.close()


def recent_traces(n: int = 60) -> list[dict]:
    c = conn()
    rows = c.execute("SELECT * FROM traces ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ---- approvals (human-in-the-loop) ----------------------------------------- #
def create_approval(agent: str, action: str, payload: dict) -> int:
    c = conn()
    cur = c.execute("INSERT INTO approvals(ts,agent,action,payload,status) "
                    "VALUES(?,?,?,?,'pending')", (_now(), agent, action, json.dumps(payload)))
    c.commit(); rid = cur.lastrowid; c.close()
    return rid


def pending_approvals() -> list[dict]:
    c = conn()
    rows = c.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY id DESC").fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:  # noqa: BLE001
            pass
        out.append(d)
    return out


def resolve_approval(approval_id: int, decision: str, by: str = "human") -> dict:
    c = conn()
    r = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not r:
        c.close(); return {"error": "not found"}
    c.execute("UPDATE approvals SET status=?, resolved_ts=?, decided_by=? WHERE id=?",
              (decision, _now(), by, approval_id))
    c.commit()
    d = dict(r); c.close()
    try:
        d["payload"] = json.loads(d["payload"])
    except Exception:  # noqa: BLE001
        pass
    d["status"] = decision
    return d


# ---- long-term kv memory --------------------------------------------------- #
def kv_set(k: str, v):
    c = conn()
    c.execute("INSERT OR REPLACE INTO kv(k,v,updated) VALUES(?,?,?)",
              (k, json.dumps(v), _now()))
    c.commit(); c.close()


def kv_get(k: str, default=None):
    c = conn()
    r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    c.close()
    return json.loads(r["v"]) if r else default


if __name__ == "__main__":
    rid = start_run("test", ["a", "b", "c"], "smoke")
    mark_step(rid, 0, "done", {"ok": 1})
    print("resumable:", resumable_run("test"))
    trace(rid, "tester", "a", "noop", "ok", 12, "did a")
    print("traces:", len(recent_traces()))
    aid = create_approval("tester", "propose_param", {"name": "X", "value": 1})
    print("pending:", len(pending_approvals()))
    print("resolve:", resolve_approval(aid, "approved")["status"])
    kv_set("lesson:1", "high_vol -> half size")
    print("kv:", kv_get("lesson:1"))
    finish_run(rid)
