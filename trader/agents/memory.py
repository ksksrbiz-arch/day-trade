"""Semantic memory for the agents, backed by Cloudflare bge embeddings.

Agents store observations/decisions/outcomes as text + vector, then recall the
most similar past situations before acting (lightweight RAG over their own
history). Storage is a JSONL file + parallel vectors; cosine similarity in pure
NumPy. Fail-soft: if embeddings are unavailable, store still records text and
recall returns recent items.
"""
from __future__ import annotations

import json
import os
import time
import numpy as np

from . import cloudflare as cf

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "agents"))
STORE = os.path.join(_DATA, "memory.jsonl")
MAX_ITEMS = 2000


def _norm(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n else v


def remember(text: str, meta: dict | None = None) -> bool:
    os.makedirs(_DATA, exist_ok=True)
    vec = cf.embed([text])
    emb = vec[0] if vec else []
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "text": text[:600], "meta": meta or {}, "emb": emb}
    with open(STORE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return bool(emb)


def _load():
    if not os.path.exists(STORE):
        return []
    out = []
    for ln in open(STORE, encoding="utf-8").read().splitlines()[-MAX_ITEMS:]:
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                pass
    return out


def recall(query: str, k: int = 3) -> list[dict]:
    items = _load()
    if not items:
        return []
    qv = cf.embed([query])
    if not qv:
        return [{"text": it["text"], "score": 0.0, "ts": it.get("ts")} for it in items[-k:]]
    q = _norm(qv[0])
    scored = []
    for it in items:
        if not it.get("emb"):
            continue
        s = float(np.dot(q, _norm(it["emb"])))
        scored.append({"text": it["text"], "score": round(s, 3),
                       "ts": it.get("ts"), "meta": it.get("meta", {})})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:k]


def stats() -> dict:
    items = _load()
    return {"items": len(items), "embedded": sum(1 for i in items if i.get("emb"))}


if __name__ == "__main__":
    remember("Regime flipped to high_vol; cut size to half and stood down on momentum entries.",
             {"kind": "decision"})
    remember("Crypto risk-off; BTC trend strong down; defensive posture worked.",
             {"kind": "outcome"})
    print("stats:", stats())
    for r in recall("what do we do when volatility spikes?", k=2):
        print(f"  {r['score']:+.3f}  {r['text'][:70]}")
