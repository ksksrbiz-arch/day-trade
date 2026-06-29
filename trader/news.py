"""
News ingestion. Polls RSS feeds, returns only items not seen before.

Reality check baked into the docstring so future-you remembers: RSS polls on
the order of minutes. This pipeline is structurally too slow to win a race
against anyone trading the same headline on a direct feed. Treat news here as a
source of *direction over hours/days*, not millisecond reaction. If your edge
thesis depends on being fast, this architecture is the wrong tool and the
backtest will tell you so.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Iterable

import feedparser


def _item_id(entry) -> str:
    basis = getattr(entry, "id", "") or getattr(entry, "link", "") or getattr(entry, "title", "")
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _load_seen(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen(path: str, seen: set[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # keep the file bounded
    trimmed = list(seen)[-5000:]
    with open(path, "w") as f:
        json.dump(trimmed, f)


def fetch(feeds: Iterable[str], seen_path: str = "data/seen.json") -> list[dict]:
    """Return new items as dicts: {id, title, summary, link, source}."""
    seen = _load_seen(seen_path)
    fresh: list[dict] = []
    for url in feeds:
        parsed = feedparser.parse(url)
        source = parsed.feed.get("title", url) if hasattr(parsed, "feed") else url
        for entry in parsed.entries:
            iid = _item_id(entry)
            if iid in seen:
                continue
            seen.add(iid)
            fresh.append({
                "id": iid,
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "link": entry.get("link", ""),
                "source": source,
            })
    _save_seen(seen_path, seen)
    return fresh
