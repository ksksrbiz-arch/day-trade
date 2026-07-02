"""
Local Long-Term Memory (LTM) -- the system's own institutional memory, with
NO external MCP server required. Replaces the Pieces dependency in the cloud.

Design goals (all satisfied here):
  * IDEMPOTENT  -- a memory's primary key is a content hash (dedup_key or
    description|summary), written with INSERT OR IGNORE. Re-running the same
    cycle (same day, same regime, same tune) never creates duplicates.
  * SEMANTIC    -- recall ranks by cosine similarity over Cloudflare Workers AI
    `bge-base-en-v1.5` embeddings (the account's free embed model).
  * DURABLE     -- SQLite at data/ltm.db, which lives on the Render persistent
    disk (/app/data), so memory survives redeploys.
  * FAIL-SOFT   -- if embeddings are unavailable, writes still persist (text +
    FTS) and recall degrades to keyword (FTS5, else recency). Nothing here can
    raise into the trading loop.

Public API mirrors the old PiecesLTM so it is a drop-in:
    ltm.remember(description, summary_md, dedup_key="") -> bool   # True if newly stored
    ltm.ask(question, topics=None)                       -> str    # recalled text ('' if none)
    ltm.recall(query, k=5)                               -> list[dict]
    ltm.stats()                                          -> dict
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("LTM_DB", str(PROJ / "data" / "ltm.db")))

_MAX_RECALL_CHARS = 4000


def _embed(text: str) -> list:
    try:
        from trader.agents import cloudflare as cf
        vecs = cf.embed([text[:2000]])
        return vecs[0] if vecs else []
    except Exception:  # noqa: BLE001
        return []


def _pack(vec) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec) if vec else b""


def _unpack(blob: bytes) -> list:
    if not blob:
        return []
    return list(struct.unpack(f"<{len(blob)//4}f", blob))


def _cos(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


_FTS_OK = None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=5.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        "CREATE TABLE IF NOT EXISTS memories("
        "id TEXT PRIMARY KEY, ts TEXT, description TEXT, summary TEXT, topics TEXT, emb BLOB)"
    )
    global _FTS_OK
    if _FTS_OK is None:
        try:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                "USING fts5(id UNINDEXED, description, summary, topics)"
            )
            _FTS_OK = True
        except sqlite3.OperationalError:
            _FTS_OK = False
    return con


def _hash(dedup_key: str, description: str, summary: str) -> str:
    key = dedup_key or (description + "|" + summary)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


class LocalLTM:
    """SQLite + Cloudflare-embeddings long-term memory. Fully fail-soft."""

    def __init__(self, embed_on_write: bool = True):
        self.embed_on_write = embed_on_write

    def remember(self, description: str, summary_md: str, dedup_key: str = "",
                 topics=None) -> bool:
        con = None
        try:
            mid = _hash(dedup_key, description, summary_md)
            con = _connect()
            if con.execute("SELECT 1 FROM memories WHERE id=?", (mid,)).fetchone():
                return False  # idempotent no-op
            emb = _embed(description + " " + summary_md) if self.embed_on_write else []
            topics_s = ",".join(topics) if topics else ""
            with con:
                con.execute(
                    "INSERT OR IGNORE INTO memories(id, ts, description, summary, topics, emb) "
                    "VALUES(?,?,?,?,?,?)",
                    (mid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     description[:1000], summary_md[:8000], topics_s, _pack(emb)))
                if _FTS_OK:
                    con.execute(
                        "INSERT INTO memories_fts(id, description, summary, topics) VALUES(?,?,?,?)",
                        (mid, description[:1000], summary_md[:8000], topics_s))
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[ltm] remember failed (fail-soft): {e}")
            return False
        finally:
            if con is not None:
                con.close()

    def recall(self, query: str, k: int = 5) -> list:
        con = None
        try:
            con = _connect()
            rows = con.execute(
                "SELECT id, ts, description, summary, topics, emb FROM memories").fetchall()
        except Exception as e:  # noqa: BLE001
            print(f"[ltm] recall failed (fail-soft): {e}")
            return []
        finally:
            if con is not None:
                con.close()
        if not rows:
            return []
        qv = _embed(query)
        if qv:
            scored = []
            for _id, ts, desc, summ, topics, emb in rows:
                s = _cos(qv, _unpack(emb))
                scored.append({"id": _id, "ts": ts, "description": desc, "summary": summ,
                               "topics": topics, "score": round(s, 4)})
            scored.sort(key=lambda x: x["score"], reverse=True)
            if scored and scored[0]["score"] > 0.0:
                return scored[:k]
        ql = [t for t in query.lower().split() if len(t) > 2]
        ranked = []
        for _id, ts, desc, summ, topics, emb in rows:
            hay = f"{desc} {summ} {topics}".lower()
            hits = sum(1 for t in ql if t in hay)
            ranked.append((hits, ts, {"id": _id, "ts": ts, "description": desc, "summary": summ,
                                      "topics": topics, "score": round(hits / (len(ql) or 1), 3)}))
        ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return [r[2] for r in ranked[:k]]

    def ask(self, question: str, topics=None) -> str:
        q = question + (" " + " ".join(topics) if topics else "")
        hits = self.recall(q, k=5)
        if not hits:
            return ""
        out, total = [], 0
        for h in hits:
            piece = f"- ({h.get('ts','')}) {h.get('description','')}: {h.get('summary','')}".strip()
            if total + len(piece) > _MAX_RECALL_CHARS:
                break
            out.append(piece)
            total += len(piece)
        return "\n".join(out)

    def stats(self) -> dict:
        con = None
        try:
            con = _connect()
            n = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            e = con.execute("SELECT COUNT(*) FROM memories WHERE length(emb) > 0").fetchone()[0]
            return {"items": int(n), "embedded": int(e), "db": str(DB_PATH), "fts": bool(_FTS_OK)}
        except Exception as e:  # noqa: BLE001
            return {"items": 0, "embedded": 0, "error": str(e)[:80]}
        finally:
            if con is not None:
                con.close()


_SHARED = None


def get_ltm() -> "LocalLTM":
    global _SHARED
    if _SHARED is None:
        _SHARED = LocalLTM()
    return _SHARED


def remember(description: str, summary_md: str, dedup_key: str = "", topics=None) -> bool:
    return get_ltm().remember(description, summary_md, dedup_key, topics)


def ask(question: str, topics=None) -> str:
    return get_ltm().ask(question, topics)


def recall(query: str, k: int = 5) -> list:
    return get_ltm().recall(query, k)


def stats() -> dict:
    return get_ltm().stats()


def available() -> bool:
    try:
        get_ltm().stats()
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    L = get_ltm()
    print("write1:", L.remember("Regime flip", "High-vol regime; halved size.", dedup_key="t1"))
    print("write1-dup:", L.remember("Regime flip", "High-vol regime; halved size.", dedup_key="t1"))
    print("write2:", L.remember("Crypto risk-off", "BTC trend down; defensive worked.", dedup_key="t2"))
    print("stats:", L.stats())
    print("ask:", L.ask("what to do when volatility spikes?"))
