"""
Clear Street (Studio) READ-ONLY client.

Scope decision (deliberate): Clear Street is a real-money prime broker. This
module is wired for DATA ONLY -- news and market data that feed the upstream
labeler/feature layers. It intentionally does NOT expose order entry. Execution
stays on Alpaca PAPER until the strategy proves an edge that beats SPY, per the
project's honest-measurement protocol. Adding live execution here would be a
one-line call, and it is left out on purpose.

Auth: OAuth2 client-credentials (https://auth.clearstreet.io/oauth/token) ->
short-lived JWT bearer token, cached until shortly before expiry.

Everything fails SOFT: if credentials are missing/invalid or the API errors,
methods return None / [] and print a note, so the trading loop is never blocked
by a data-source problem.

NOTE: requires BOTH client_id and client_secret (the values in the
clearstreet-api-*.json file Clear Street issues). A single token string is not
enough for the client-credentials grant.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

AUTH_URL = "https://auth.clearstreet.io/oauth/token"
BASE_URL = "https://api.clearstreet.io"
AUDIENCE = "https://api.clearstreet.io"
_UA = "paper-trader/1.0"


class ClearStreetClient:
    def __init__(self, client_id: str, client_secret: str,
                 audience: str = AUDIENCE, base_url: str = BASE_URL,
                 auth_url: str = AUTH_URL):
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.enabled = bool(client_id and client_secret)
        self._token: Optional[str] = None
        self._exp: float = 0.0

    # --- auth ---
    def _bearer(self) -> Optional[str]:
        if not self.enabled:
            return None
        if self._token and time.time() < self._exp - 60:
            return self._token
        body = json.dumps({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "audience": self.audience,
        }).encode()
        req = urllib.request.Request(
            self.auth_url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": _UA},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            self._token = d["access_token"]
            self._exp = time.time() + int(d.get("expires_in", 3600))
            return self._token
        except Exception as e:
            print(f"[clearstreet] auth failed (fail-soft): {e}")
            return None

    def can_auth(self) -> bool:
        return self._bearer() is not None

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        tok = self._bearer()
        if not tok:
            return None
        url = f"{self.base_url}{path}"
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {tok}", "User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"[clearstreet] GET {path} failed (fail-soft): {e}")
            return None

    # --- data: news (feeds the labeler) ---
    def news(self, text: Optional[str] = None, symbol: Optional[str] = None,
             limit: int = 25) -> list[dict]:
        """Return news items normalised to the labeler's shape:
        {id, title, summary, tickers}. Empty list on any failure.
        """
        data = self._get("/studio/v1/news", {
            "text": text, "symbol": symbol, "limit": limit,
        })
        if not data:
            return []
        raw = data.get("data") or data.get("news") or data.get("items") or []
        out = []
        for n in raw:
            out.append({
                "id": str(n.get("id") or n.get("uuid") or n.get("url") or n.get("title", "")),
                "title": n.get("title") or n.get("headline") or "",
                "summary": n.get("summary") or n.get("description") or n.get("body", "")[:500],
                "tickers": n.get("symbols") or n.get("tickers") or [],
            })
        return out

    # --- data: latest daily bar (feeds marketdata as an alt source) ---
    def daily(self, symbol: str) -> Optional[dict]:
        data = self._get(f"/studio/v1/market-data/{symbol}/bars",
                         {"timeframe": "1d", "limit": 1})
        if not data:
            return None
        bars = data.get("data") or data.get("bars") or []
        return bars[-1] if bars else None
