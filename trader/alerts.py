"""Alerts engine -- surface the FEW things a human should actually look at.

The platform emits a flood of signals; this layer distills the operationally and
strategically notable events into a small, deduped, severity-ranked alert feed:

  service down / crash-loop / self-test fail   (operational, from supervisor health)
  regime change                                (market state shifted)
  autonomy applied                             (the desk changed its own config)
  edge verdict reached                         (a signal source matured to EDGE / negative)

Each rule is fail-soft and idempotent (one alert per condition per day), so the
feed never spams. fire() evaluates all rules and persists new alerts; the runtime
calls it each cycle. Surfaced via /api/alerts and the dashboard bell.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "data", "alerts"))
DB = os.environ.get("ALERTS_DB", os.path.join(_DATA, "alerts.db"))
STATE = os.path.join(_DATA, "state.json")

SEV = {"info": 0, "warn": 1, "critical": 2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, ts TEXT, day TEXT, severity TEXT, kind TEXT, text TEXT, ack INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts);
"""


def _conn():
    os.makedirs(_DATA, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(d: dict):
    try:
        os.makedirs(_DATA, exist_ok=True)
        json.dump(d, open(STATE, "w"))
    except Exception:  # noqa: BLE001
        pass


def _put(c, kind: str, severity: str, text: str, key: str) -> dict | None:
    """Idempotent per (kind,key,day). Returns the alert dict if newly created."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    aid = hashlib.sha1(f"{kind}|{key}|{day}".encode()).hexdigest()[:16]
    cur = c.execute("INSERT OR IGNORE INTO alerts(id,ts,day,severity,kind,text,ack) VALUES(?,?,?,?,?,?,0)",
                    (aid, _now(), day, severity, kind, text[:240]))
    if cur.rowcount > 0:
        return {"id": aid, "severity": severity, "kind": kind, "text": text[:240]}
    return None


def fire() -> dict:
    """Evaluate all rules; persist + return new alerts."""
    c = _conn()
    new: list[dict] = []
    st = _load_state()

    def add(kind, sev, text, key):
        a = _put(c, kind, sev, text, key)
        if a:
            new.append(a)

    # --- operational: supervisor health ---
    try:
        from .agents import state as agstate
        h = agstate.kv_get("system_health", {}) or {}
        for svc, ok in (h.get("services") or {}).items():
            if ok is False:
                add("service", "critical", f"service '{svc}' is DOWN", f"{svc}:down")
            elif ok == "crash_loop":
                add("service", "critical", f"service '{svc}' in crash-loop backoff", f"{svc}:loop")
        stf = h.get("selftest") or {}
        if stf and not stf.get("ok", True):
            add("selftest", "warn", "self-test failing: " + ", ".join(stf.get("fails", [])), "selftest")
    except Exception:  # noqa: BLE001
        pass

    # --- regime change ---
    try:
        from . import market_brain
        reg = market_brain.cached_regime("neutral")
        last = st.get("regime")
        if last and reg != last:
            sev = "warn" if reg in ("high_vol", "risk_off") else "info"
            add("regime", sev, f"market regime shifted: {last} → {reg}", f"{last}->{reg}")
        st["regime"] = reg
    except Exception:  # noqa: BLE001
        pass

    # --- autonomy applied a change ---
    try:
        from . import autonomy
        for a in autonomy.recent_audit(10):
            if a.get("status") == "applied":
                add("autonomy", "warn", f"autonomy applied {a.get('action')}: {a.get('reason', '')}",
                    f"{a.get('action')}|{a.get('ts')}")
    except Exception:  # noqa: BLE001
        pass

    # --- edge verdict reached (a source matured) ---
    try:
        from . import edge
        for s in edge.report().get("sources", []):
            v = s.get("verdict", "")
            if v == "EDGE":
                add("edge", "info", f"{s['source']} reached a positive forward EDGE", f"{s['source']}:edge")
            elif v == "negative":
                add("edge", "warn", f"{s['source']} is forward-NEGATIVE (consider muting)", f"{s['source']}:neg")
    except Exception:  # noqa: BLE001
        pass

    # --- mesh anomalies (salience spikes, layer silence, volume bursts) ---
    try:
        from . import mesh_anomaly
        for a in mesh_anomaly.anomalies(150):
            sev = "warn" if a.get("severity") == "warn" else "info"
            add("mesh_anomaly", sev, f"mesh: {a.get('text', '')}", f"{a.get('kind')}:{a.get('layer', '')}")
    except Exception:  # noqa: BLE001
        pass

    # --- mesh layer SLA: a layer's feeding daemon went quiet ---
    try:
        from . import mesh_sla
        for s in mesh_sla.overdue():
            if s.get("status") == "stale":
                add("mesh_sla", "warn", f"mesh layer '{s.get('layer')}' is stale "
                    f"({int(s.get('last_seen_min', 0))}m since last insight)", f"{s.get('layer')}:stale")
    except Exception:  # noqa: BLE001
        pass

    c.commit()
    c.close()
    _save_state(st)
    # mirror criticals to the mesh
    if new:
        try:
            from . import mesh
            for a in new:
                if a["severity"] == "critical":
                    mesh.publish("alerts", a["kind"], a["text"], salience=0.85)
        except Exception:  # noqa: BLE001
            pass
    return {"new": len(new), "alerts": new}


def recent(limit: int = 50, unack_only: bool = False) -> list[dict]:
    c = _conn()
    q = "SELECT * FROM alerts"
    if unack_only:
        q += " WHERE ack=0"
    q += " ORDER BY ts DESC LIMIT ?"
    rows = c.execute(q, (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def counts() -> dict:
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM alerts WHERE ack=0").fetchone()[0]
    crit = c.execute("SELECT COUNT(*) FROM alerts WHERE ack=0 AND severity='critical'").fetchone()[0]
    warn = c.execute("SELECT COUNT(*) FROM alerts WHERE ack=0 AND severity='warn'").fetchone()[0]
    c.close()
    return {"unack": total, "critical": crit, "warn": warn}


def ack(alert_id: str | None = None) -> dict:
    c = _conn()
    if alert_id and alert_id != "all":
        c.execute("UPDATE alerts SET ack=1 WHERE id=?", (alert_id,))
    else:
        c.execute("UPDATE alerts SET ack=1 WHERE ack=0")
    n = c.total_changes
    c.commit(); c.close()
    return {"acked": n}


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception:
        pass
    print("fire:", fire())
    print("counts:", counts())
    for a in recent(10):
        print(f"  [{a['severity']:8}] {a['kind']}: {a['text'][:70]}")
