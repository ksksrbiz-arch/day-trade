"""SQLite schema + connection for the CRSP-lite security master."""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data"))
DB_PATH = os.environ.get("CRSP_DB_PATH", os.path.join(_DATA, "crsp_lite.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Permanent security identity (CRSP PERMNO analogue).
CREATE TABLE IF NOT EXISTS securities (
    permno      INTEGER PRIMARY KEY AUTOINCREMENT,
    cik         TEXT,                 -- SEC CIK: strongest permanent anchor
    ticker      TEXT,                 -- most-recent / canonical ticker
    name        TEXT,
    exchange    TEXT,
    asset_type  TEXT,
    ipo_date    TEXT,
    delist_date TEXT,
    status      TEXT DEFAULT 'active',-- active | delisted
    first_seen  TEXT,
    last_seen   TEXT,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_sec_ticker ON securities(ticker);
CREATE INDEX IF NOT EXISTS ix_sec_cik    ON securities(cik);

-- Ticker symbol over time -> permno (handles renames / reuse of symbols).
CREATE TABLE IF NOT EXISTS ticker_history (
    permno     INTEGER,
    ticker     TEXT,
    start_date TEXT,
    end_date   TEXT,
    source     TEXT,
    FOREIGN KEY(permno) REFERENCES securities(permno)
);
CREATE INDEX IF NOT EXISTS ix_th_ticker ON ticker_history(ticker);

-- Point-in-time index membership (the survivorship-bias killer).
CREATE TABLE IF NOT EXISTS membership (
    permno     INTEGER,
    ticker     TEXT,
    index_name TEXT,        -- e.g. 'SP500'
    start_date TEXT,        -- date added (inclusive)
    end_date   TEXT,        -- date removed (NULL = still a member)
    source     TEXT,
    FOREIGN KEY(permno) REFERENCES securities(permno)
);
CREATE INDEX IF NOT EXISTS ix_mem_idx  ON membership(index_name);
CREATE INDEX IF NOT EXISTS ix_mem_tkr  ON membership(ticker);

-- Delisting events, cross-referenced + AI-classified.
CREATE TABLE IF NOT EXISTS delistings (
    permno        INTEGER,
    ticker        TEXT,
    delist_date   TEXT,
    reason_raw    TEXT,
    reason_class  TEXT,      -- merger|acquisition|bankruptcy|going_private|listing_rule|other
    ai_confidence REAL,
    source        TEXT,
    FOREIGN KEY(permno) REFERENCES securities(permno)
);
CREATE INDEX IF NOT EXISTS ix_del_tkr ON delistings(ticker);

-- Daily prices (incl. delisted names so backtests are bias-reduced).
CREATE TABLE IF NOT EXISTS prices (
    permno    INTEGER,
    date      TEXT,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    adj_close REAL,
    volume    REAL,
    source    TEXT,
    PRIMARY KEY (permno, date)
);

-- Free-form AI / cross-source enrichment (key/value with provenance).
CREATE TABLE IF NOT EXISTS enrichment (
    permno     INTEGER,
    key        TEXT,
    value      TEXT,
    confidence REAL,
    model      TEXT,
    created_at TEXT,
    FOREIGN KEY(permno) REFERENCES securities(permno)
);
CREATE INDEX IF NOT EXISTS ix_enr ON enrichment(permno, key);

-- Cross-source agreement / conflict ledger (data-quality audit trail).
CREATE TABLE IF NOT EXISTS reconciliation (
    permno     INTEGER,
    field      TEXT,
    sources    TEXT,      -- json: {source: value}
    agreed     INTEGER,   -- 1 if all agree
    chosen     TEXT,
    confidence REAL,
    created_at TEXT
);

-- Provenance: every source run logged for idempotent rebuilds.
CREATE TABLE IF NOT EXISTS source_runs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    ran_at TEXT,
    rows   INTEGER,
    ok     INTEGER,
    note   TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | None = None) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def log_run(conn, source: str, rows: int, ok: bool, note: str = "") -> None:
    conn.execute(
        "INSERT INTO source_runs(source,ran_at,rows,ok,note) VALUES(?,?,?,?,?)",
        (source, now_iso(), int(rows), 1 if ok else 0, note[:500]),
    )
    conn.commit()


if __name__ == "__main__":
    c = init_db()
    tabs = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("DB:", DB_PATH)
    print("tables:", ", ".join(tabs))
