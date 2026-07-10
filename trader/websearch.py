"""
websearch.py -- the brain's ability to LOOK ANYTHING UP, on its own, at any time.

No API key, no third-party MCP: a direct query to DuckDuckGo's keyless HTML
endpoints (html. then lite. as fallback), parsed with the stdlib. Returns a
short list of {title, url, snippet}. Cached + rate-limited + fail-soft so it can
be called freely from the reasoner / agents / reflection loop without ever
throwing into the trading path.
"""
from __future__ import annotations

import html as _html
import re
import time
import urllib.parse
import urllib.request

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_TTL = 900.0
_MIN_INTERVAL = 1.5          # politeness: >=1.5s between live queries
_cache: dict = {}
_last = {"at": 0.0}


def _clean(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _decode_uddg(href: str) -> str:
    """DDG wraps result links as /l/?uddg=<encoded>. Unwrap to the real URL."""
    if "uddg=" in href:
        try:
            q = urllib.parse.urlparse(href).query
            u = urllib.parse.parse_qs(q).get("uddg", [""])[0]
            return urllib.parse.unquote(u)
        except Exception:  # noqa: BLE001
            return href
    return href if href.startswith("http") else ("https:" + href if href.startswith("//") else href)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read().decode("utf-8", "replace")


def _parse_html(page: str) -> list:
    out = []
    # each result: <a ... class="result__a" href="...">TITLE</a> ... snippet
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)',
        page, re.S,
    ):
        href, title, tail = m.group(1), _clean(m.group(2)), m.group(3)
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', tail, re.S)
        snippet = _clean(sm.group(1)) if sm else ""
        url = _decode_uddg(href)
        if title and url.startswith("http"):
            out.append({"title": title[:200], "url": url, "snippet": snippet[:400]})
    return out


def _parse_lite(page: str) -> list:
    out = []
    for m in re.finditer(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href, title = _decode_uddg(m.group(1)), _clean(m.group(2))
        if title and href.startswith("http"):
            out.append({"title": title[:200], "url": href, "snippet": ""})
    return out


def search(query: str, k: int = 5) -> list:
    """Look up `query` on DuckDuckGo. Returns up to k {title,url,snippet}. []-safe."""
    query = (query or "").strip()
    if not query:
        return []
    now = time.time()
    hit = _cache.get(query)
    if hit and now - hit[0] < _TTL:
        return hit[1][:k]
    # politeness throttle
    wait = _MIN_INTERVAL - (now - _last["at"])
    if wait > 0:
        time.sleep(min(wait, _MIN_INTERVAL))
    _last["at"] = time.time()

    q = urllib.parse.urlencode({"q": query, "kl": "us-en"})
    results: list = []
    for url, parser in ((f"https://html.duckduckgo.com/html/?{q}", _parse_html),
                        (f"https://lite.duckduckgo.com/lite/?{q}", _parse_lite)):
        try:
            page = _fetch(url)
            results = parser(page)
            if results:
                break
        except Exception:  # noqa: BLE001
            continue
    if results:
        _cache[query] = (now, results)
    return results[:k]


def answer(query: str, k: int = 4) -> str:
    """Compact text digest of the top hits -- convenient to splice into a prompt."""
    hits = search(query, k)
    if not hits:
        return ""
    return "\n".join(f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits)


if __name__ == "__main__":
    import json
    print(json.dumps(search("SPY market outlook today", 4), indent=2))
