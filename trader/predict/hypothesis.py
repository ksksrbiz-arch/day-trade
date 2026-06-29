"""Free-AI extraction of structured, actionable hypotheses from noisy chatter
(WallStreetBets posts, news headlines).

Each hypothesis is a concrete falsifiable claim:
    {symbol, direction(up|down), magnitude_pct, horizon_days, confidence, rationale}

Uses Cloudflare 70B (fallback Groq). Tickers are validated against the CRSP-lite
master + crypto majors so we don't act on slang. Fail-soft: returns [] on error.
"""
from __future__ import annotations

import json
import re

CRYPTO = {"BTC": "BTC/USD", "ETH": "ETH/USD", "SOL": "SOL/USD", "XRP": "XRP/USD",
          "DOGE": "DOGE/USD", "ADA": "ADA/USD", "AVAX": "AVAX/USD", "LINK": "LINK/USD"}

_SYS = (
    "You are a markets analyst. From these social/news snippets, extract ONLY "
    "concrete, falsifiable trade hypotheses. For each, output a JSON object: "
    '{"symbol":"<TICKER>","direction":"up|down","magnitude_pct":<number>,'
    '"horizon_days":<int>,"confidence":<0..1>,"rationale":"<short>"}. '
    "Use real US stock tickers or crypto symbols (BTC, ETH, SOL...). Ignore vague "
    "hype with no ticker or no direction. Reply ONLY as JSON: {\"hypotheses\":[...]}."
)


def _valid_equities() -> set[str]:
    try:
        from ..crsp.schema import connect
        c = connect()
        rows = c.execute("SELECT DISTINCT ticker FROM securities WHERE ticker IS NOT NULL").fetchall()
        c.close()
        return {r[0] for r in rows if r[0]}
    except Exception:  # noqa: BLE001
        return set()


def _parse_json(txt: str) -> dict:
    if not txt:
        return {}
    t = txt.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return {}
    return {}


def extract(snippets: list[str]) -> list[dict]:
    if not snippets:
        return []
    try:
        from ..agents import cloudflare as cf
    except Exception:  # noqa: BLE001
        return []
    prompt = _SYS + "\n\nSNIPPETS:\n" + "\n".join(f"- {s[:160]}" for s in snippets[:25])
    raw = cf.chat(prompt, max_tokens=700, temperature=0.1) if cf.available() else ""
    if not raw or raw.startswith("[cf"):
        # fallback to groq
        try:
            from ..agents.orchestrator import _groq_complete
            raw = _groq_complete(prompt)
        except Exception:  # noqa: BLE001
            raw = ""
    obj = _parse_json(raw)
    arr = obj.get("hypotheses", obj if isinstance(obj, list) else [])
    valid_eq = _valid_equities()
    out = []
    for h in arr:
        if not isinstance(h, dict):
            continue
        sym = str(h.get("symbol", "")).upper().lstrip("$").strip()
        direction = str(h.get("direction", "")).lower().strip()
        if direction not in ("up", "down") or not sym:
            continue
        asset = "equity"
        if sym in CRYPTO:
            sym, asset = CRYPTO[sym], "crypto"
        elif "/" in sym:
            asset = "crypto"
        elif sym not in valid_eq:
            continue   # unknown ticker -> skip
        try:
            mag = float(h.get("magnitude_pct", 5) or 5)
            hz = int(h.get("horizon_days", 5) or 5)
            conf = float(h.get("confidence", 0.5) or 0.5)
        except (TypeError, ValueError):
            mag, hz, conf = 5.0, 5, 0.5
        out.append({"symbol": sym, "asset": asset, "direction": direction,
                    "magnitude_pct": max(0.5, min(80, mag)),
                    "horizon_days": max(1, min(30, hz)),
                    "confidence": max(0.0, min(1.0, conf)),
                    "rationale": str(h.get("rationale", ""))[:200]})
    return out


if __name__ == "__main__":
    demo = ["WEN about to rip, loading calls, easy 15% this week",
            "NVDA earnings will disappoint, puts printing",
            "to the moon yolo diamond hands",  # noise -> ignored
            "BTC breaking out, 80k incoming next month"]
    for h in extract(demo):
        print(h)
