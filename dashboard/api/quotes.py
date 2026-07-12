"""Real-time quote endpoints — snapshot + SSE delta stream.

Backed by the in-process :data:`trader.quotestream.hub`. The stream clones the
established SSE pattern in ``dashboard/app.py`` (`telemetry_stream`) but pushes
only quotes whose version changed since the client's cursor, so it stays cheap.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from trader.quotestream import hub

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


@router.get("")
def quotes_snapshot(symbols: str = Query("", description="comma-separated tickers")):
    """Latest cached quote for each requested symbol (all if none given)."""
    hub.ensure_started()
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] or None
    # Requesting a symbol also subscribes the live stream to it, so the SSE feed
    # starts pushing its quotes (used by panel header live-quote chips).
    for s in (syms or []):
        hub.ensure_symbol(s)
    return {"quotes": hub.snapshot(syms), "version": hub.version}


@router.get("/status")
def quotes_status():
    """Hub diagnostics: whether the subscriber is live and how many symbols."""
    return hub.status()


@router.get("/stream")
async def quotes_stream():
    """SSE feed of quote deltas. Each frame is ``data: {quote}\\n\\n``."""
    hub.ensure_started()

    async def gen():
        cursor = 0
        yield "retry: 3000\n\n"
        while True:
            try:
                changes, cursor = await asyncio.to_thread(hub.changes_since, cursor)
                if changes:
                    for q in changes:
                        yield "data: " + json.dumps(q) + "\n\n"
                else:
                    yield ": keepalive\n\n"
            except Exception as ex:  # noqa: BLE001
                yield "data: " + json.dumps({"error": str(ex)[:80]}) + "\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
