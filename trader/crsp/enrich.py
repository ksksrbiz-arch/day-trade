"""AI enrichment: classify *why* each name left the index/market (the single
most useful field CRSP charges for and free feeds lack). Uses Groq (free, fast)
in batches. Idempotent: only classifies delistings whose reason_class is NULL.

Categories: merger | acquisition | bankruptcy | going_private |
            listing_rule | spinoff | rename | other
This lets the backtester treat a merger-exit (capital returned) very
differently from a bankruptcy-exit (capital destroyed) -- a real bias control.
"""
from __future__ import annotations
import json
import os
import urllib.request

from .schema import init_db, now_iso

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
CATS = {"merger", "acquisition", "bankruptcy", "going_private",
        "listing_rule", "spinoff", "rename", "other"}

SYS = (
    "You are a securities-database analyst. For each company that left a stock "
    "index or was delisted, classify the most likely REASON using exactly one "
    "label from: merger, acquisition, bankruptcy, going_private, listing_rule, "
    "spinoff, rename, other. Use the company name and any raw note. Reply ONLY "
    "with a JSON array of objects {\"i\":<index>,\"reason\":<label>,"
    "\"confidence\":<0..1>}. No prose."
)


def _groq(payload: dict, timeout: int = 40) -> dict:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("no GROQ_API_KEY")
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        GROQ_URL, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (paper-trader)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _classify_batch(items: list[dict]) -> list[dict]:
    """items: [{i, name, raw, date}] -> [{i, reason, confidence}]."""
    lines = [f'{it["i"]}: name="{it["name"]}" note="{it.get("raw","")}" '
             f'date={it.get("date","")}' for it in items]
    payload = {
        "model": MODEL, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": "Classify these:\n" + "\n".join(lines) +
             '\nReturn {"results":[...]}.'},
        ],
    }
    data = _groq(payload)
    txt = data["choices"][0]["message"]["content"]
    obj = json.loads(txt)
    arr = obj.get("results", obj if isinstance(obj, list) else [])
    out = []
    for r in arr:
        reason = str(r.get("reason", "other")).lower().strip()
        if reason not in CATS:
            reason = "other"
        out.append({"i": int(r.get("i", -1)), "reason": reason,
                    "confidence": float(r.get("confidence", 0.5))})
    return out


def enrich_delistings(conn=None, limit: int = 200, batch: int = 25, verbose=True):
    """Classify up to `limit` unclassified delistings. Returns count done."""
    conn = conn or init_db()
    c = conn.cursor()
    rows = c.execute(
        "SELECT d.rowid AS rid, d.permno, d.ticker, d.delist_date, d.reason_raw, "
        "s.name FROM delistings d LEFT JOIN securities s ON s.permno=d.permno "
        "WHERE d.reason_class IS NULL ORDER BY d.delist_date DESC LIMIT ?",
        (limit,)).fetchall()
    if not rows:
        if verbose:
            print("  nothing to enrich")
        return 0
    done = 0
    for k in range(0, len(rows), batch):
        chunk = rows[k:k + batch]
        items = [{"i": j, "name": r["name"] or r["ticker"], "raw": r["reason_raw"] or "",
                  "date": r["delist_date"]} for j, r in enumerate(chunk)]
        try:
            res = _classify_batch(items)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  batch {k}: groq failed ({e}); skipping")
            continue
        by_i = {r["i"]: r for r in res}
        for j, r in enumerate(chunk):
            cl = by_i.get(j)
            if not cl:
                continue
            c.execute("UPDATE delistings SET reason_class=?, ai_confidence=? WHERE rowid=?",
                      (cl["reason"], round(cl["confidence"], 2), r["rid"]))
            c.execute("INSERT INTO enrichment(permno,key,value,confidence,model,created_at)"
                      " VALUES(?,?,?,?,?,?)",
                      (r["permno"], "delist_reason", cl["reason"],
                       round(cl["confidence"], 2), MODEL, now_iso()))
            done += 1
        conn.commit()
        if verbose:
            print(f"  classified {done}/{len(rows)}")
    return done


if __name__ == "__main__":
    conn = init_db()
    n = enrich_delistings(conn, limit=int(os.environ.get("ENRICH_LIMIT", "75")))
    print(f"enriched {n} delistings")
    for r in conn.execute("SELECT reason_class,COUNT(*) c FROM delistings "
                          "WHERE reason_class IS NOT NULL GROUP BY reason_class ORDER BY c DESC"):
        print(f"  {r[0]:14s} {r[1]}")
