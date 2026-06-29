"""
Pieces Long-Term Memory (LTM) connector -- the system's institutional memory.

Talks to the local Pieces MCP server (streamable-HTTP JSON-RPC) so the platform
can (a) WRITE durable memories of what it saw/decided and (b) ASK its own past
to inform new decisions. This is how the system "improves throughout": the
Market Brain recalls how similar regimes played out; the autotuner records every
parameter change with rationale; the council can consult history.

IDEMPOTENT BY DESIGN: writes are de-duplicated by a content hash kept in
data/ltm_seen.json, so re-running a cycle (same day, same regime, same tune)
never creates duplicate memories. Every call is independent + fail-soft: if
Pieces is offline, the rest of the system is unaffected.
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
import urllib.error
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SEEN = PROJ / "data" / "ltm_seen.json"
DEFAULT_URL = "http://localhost:39300/model_context_protocol/2025-03-26/mcp"


class PiecesLTM:
    def __init__(self, url: str = DEFAULT_URL, enabled: bool = True, timeout: float = 60.0):
        self.url = url
        self.enabled = enabled and bool(url)
        self.timeout = timeout

    # --- low-level MCP-over-HTTP (stateless: init + call each time) ---
    def _rpc(self, sid, method, params=None, notify=False):
        body = {"jsonrpc": "2.0", "method": method}
        if not notify:
            body["id"] = 1
        if params is not None:
            body["params"] = params
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", "User-Agent": "paper-trader/1.0"}
        if sid:
            h["Mcp-Session-Id"] = sid
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=h, method="POST")
        r = urllib.request.urlopen(req, timeout=self.timeout)
        return dict(r.getheaders()), (r.read().decode() if not notify else "")

    def _call_tool(self, name, args) -> str:
        if not self.enabled:
            return ""
        try:
            hd, _ = self._rpc(None, "initialize",
                              {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "paper-trader", "version": "1.0"}})
            sid = hd.get("Mcp-Session-Id") or hd.get("mcp-session-id")
            self._rpc(sid, "notifications/initialized", notify=True)
            _, body = self._rpc(sid, "tools/call", {"name": name, "arguments": args})
            # body may be JSON or SSE ("data: {...}")
            text = body
            if "data:" in body and "{" in body:
                for line in body.splitlines():
                    if line.startswith("data:"):
                        text = line[5:].strip(); break
            d = json.loads(text)
            parts = d.get("result", {}).get("content", [])
            return " ".join(p.get("text", "") for p in parts if p.get("type") == "text")[:4000]
        except Exception as e:
            print(f"[pieces] {name} failed (fail-soft): {e}")
            return ""

    # --- public API ---
    def ask(self, question: str, topics=None) -> str:
        """Query the long-term memory. Returns recalled text (or '')."""
        return self._call_tool("ask_pieces_ltm",
                               {"question": question, "topics": topics or []})

    def remember(self, description: str, summary_md: str, dedup_key: str = "") -> bool:
        """Idempotently store a memory. Returns True if written, False if a
        duplicate (by dedup_key or content hash) was skipped."""
        if not self.enabled:
            return False
        key = dedup_key or (description + "|" + summary_md)
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        seen = _load_seen()
        if h in seen:
            return False
        out = self._call_tool("create_pieces_memory",
                              {"summary_description": description, "summary": summary_md})
        # mark seen even on best-effort write to preserve idempotency for the day
        seen.add(h); _save_seen(seen)
        return True


def _load_seen() -> set:
    if SEEN.exists():
        try:
            return set(json.loads(SEEN.read_text()))
        except Exception:
            return set()
    return set()


def _save_seen(s: set) -> None:
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(list(s)[-5000:]))
