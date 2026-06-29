"""
Clear Street Omni AI -- READ-ONLY research client.

Omni is Clear Street's financial copilot (live market data, news, options
analysis, and — deliberately NOT used here — order execution). We call it with
an EMPTY `capabilities` array, which means Omni cannot emit order tickets
(PREFILL_ORDER) or any actionable UI through this client: it can only return
text research. This client never places, confirms, or cancels orders.

Auth: the Clear Street API token is a DIRECT Bearer token (no OAuth exchange).
Endpoints (api.clearstreet.com):
  POST /v1/omni-ai/threads                      {account_id:int, type, text, capabilities:[]}
  GET  /v1/omni-ai/responses/{id}?account_id=   poll until terminal
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error

BASE = "https://api.clearstreet.com"
_UA = "paper-trader/1.0"


class OmniClient:
    def __init__(self, token: str, account_id, base: str = BASE, timeout: float = 30.0):
        self.token = token
        try:
            self.account_id = int(account_id) if account_id else None
        except (TypeError, ValueError):
            self.account_id = None
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.enabled = bool(token and self.account_id)

    def _req(self, method, ep, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + ep, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": _UA,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def ask(self, text: str, mode: str = "INSTANT", poll_secs: float = 2.0,
            max_polls: int = 40) -> dict:
        """Ask Omni a research question. Returns {status,text,followups,thread_id}
        or {error}. ALWAYS text-only (capabilities=[]) -> no order tickets."""
        if not self.enabled:
            return {"error": "omni disabled (missing CLEARSTREET_TOKEN / account id)"}
        try:
            r = self._req("POST", "/v1/omni-ai/threads",
                          {"account_id": self.account_id, "type": mode,
                           "text": text, "capabilities": []})
            d = r.get("data", r)
            rid, tid = d.get("response_id"), d.get("thread_id")
            if not rid:
                return {"error": "no response_id", "raw": d}
            for _ in range(max_polls):
                time.sleep(poll_secs)
                rr = self._req("GET", f"/v1/omni-ai/responses/{rid}?account_id={self.account_id}")
                dd = rr.get("data", rr)
                st = dd.get("status")
                if st in ("succeeded", "failed", "canceled"):
                    parts = (dd.get("content") or {}).get("parts", [])
                    txt = " ".join(p.get("text", "") for p in parts
                                   if p.get("type") == "text").strip()
                    follow = []
                    for p in parts:
                        if p.get("type") == "suggested_actions":
                            follow = [b.get("label") for b in
                                      p.get("payload", {}).get("actionButtons", [])]
                    return {"status": st, "text": txt, "followups": follow, "thread_id": tid}
            return {"error": "timeout waiting for Omni"}
        except urllib.error.HTTPError as e:
            return {"error": f"http {e.code}", "body": e.read().decode()[:200]}
        except Exception as e:
            return {"error": str(e)[:200]}


# --- research enrichment helpers (read-only) ---

_BULL = ("bullish", "upside", "positive", "rally", "outperform", "upgrade",
         "beat", "strong", "tailwind", "accumulate", "constructive")
_BEAR = ("bearish", "downside", "negative", "sell-off", "selloff", "underperform",
         "downgrade", "miss", "weak", "headwind", "caution", "overvalued")


def parse_stance(text: str) -> str:
    """Reduce Omni prose to a deterministic stance: bullish | bearish | neutral.
    Honors an explicit leading word, else uses a bull/bear term tally."""
    t = (text or "").strip().lower()
    if not t:
        return "neutral"
    head = t.split()[0].strip(".:,")
    if head in ("bullish", "bearish", "neutral"):
        return head
    b = sum(t.count(w) for w in _BULL)
    s = sum(t.count(w) for w in _BEAR)
    if b >= s + 2:
        return "bullish"
    if s >= b + 2:
        return "bearish"
    return "neutral"


def opposes(side: str, stance: str) -> bool:
    """Does Omni's stance clearly contradict the proposed side?"""
    return (side == "buy" and stance == "bearish") or (side == "sell" and stance == "bullish")


class _ResearchMixin:
    pass


def research(client: "OmniClient", symbol: str, side: str) -> dict:
    """Ask Omni for a fast stance on a proposed trade. Returns
    {stance, text} (read-only; capabilities stay empty)."""
    word = "long" if side == "buy" else "short"
    prompt = (f"Reply with ONE word first — BULLISH, BEARISH, or NEUTRAL — on a "
              f"{word} in {symbol} over the next few days, then one short sentence why.")
    r = client.ask(prompt, mode="INSTANT", poll_secs=2.0, max_polls=20)
    if r.get("error"):
        return {"stance": "neutral", "text": r["error"], "ok": False}
    txt = r.get("text", "")
    return {"stance": parse_stance(txt), "text": txt[:240], "ok": True}
