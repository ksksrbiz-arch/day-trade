"""Fundamental-analysis engine.

Pulls Alpha Vantage company OVERVIEW (valuation, profitability, growth) and
turns it into a fundamental_score in [-1, 1] across three pillars: VALUE
(cheap?), QUALITY (profitable?), GROWTH (expanding?).

Alpha Vantage's free tier is heavily rate-limited, so every fetch is cached
into the CRSP-lite SQLite DB (enrichment table, keyed by permno) and re-used
for `max_age_days`. The scoring math (`score_overview`) is a PURE function of
the overview dict, so it is unit-testable and never touches the network.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

_AV = "https://www.alphavantage.co/query"


def _f(d: dict, key: str):
    v = d.get(key)
    if v in (None, "None", "-", "", "nan"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _band(x, lo, hi):
    """Map x in [lo,hi] -> [-1,1] (lo->-1, hi->+1), clamped."""
    if x is None:
        return None
    if hi == lo:
        return 0.0
    return max(-1.0, min(1.0, 2 * (x - lo) / (hi - lo) - 1))


@dataclass
class Fundamentals:
    ticker: str
    pe: float | None
    peg: float | None
    pb: float | None
    roe: float | None
    profit_margin: float | None
    rev_growth_yoy: float | None
    eps_growth_yoy: float | None
    value_score: float
    quality_score: float
    growth_score: float
    fundamental_score: float      # composite [-1,1]
    label: str

    def as_log(self) -> dict:
        return asdict(self)


def score_overview(ov: dict) -> Fundamentals:
    """Pure scoring of an Alpha Vantage OVERVIEW dict."""
    pe = _f(ov, "PERatio")
    peg = _f(ov, "PEGRatio")
    pb = _f(ov, "PriceToBookRatio")
    roe = _f(ov, "ReturnOnEquityTTM")
    pm = _f(ov, "ProfitMargin")
    rg = _f(ov, "QuarterlyRevenueGrowthYOY")
    eg = _f(ov, "QuarterlyEarningsGrowthYOY")

    # VALUE: cheaper = better, so invert the bands.
    v_votes = []
    if pe is not None and pe > 0:
        v_votes.append(-_band(min(pe, 60), 5, 40))     # PE 5 cheap -> +1, 40 rich -> -1
    if peg is not None and peg > 0:
        v_votes.append(-_band(min(peg, 5), 0.5, 3))
    if pb is not None and pb > 0:
        v_votes.append(-_band(min(pb, 15), 1, 8))
    value = round(sum(v_votes) / len(v_votes), 3) if v_votes else 0.0

    # QUALITY: higher profitability = better.
    q_votes = []
    if roe is not None:
        q_votes.append(_band(roe, 0.0, 0.30))          # ROE 0 -> -1, 30% -> +1
    if pm is not None:
        q_votes.append(_band(pm, 0.0, 0.25))
    quality = round(sum(q_votes) / len(q_votes), 3) if q_votes else 0.0

    # GROWTH: higher YoY growth = better.
    g_votes = []
    if rg is not None:
        g_votes.append(_band(rg, -0.05, 0.30))
    if eg is not None:
        g_votes.append(_band(eg, -0.10, 0.40))
    growth = round(sum(g_votes) / len(g_votes), 3) if g_votes else 0.0

    parts = [p for p in (value, quality, growth) if p is not None]
    comp = round(sum(parts) / len(parts), 3) if parts else 0.0
    label = ("strong" if comp >= 0.4 else "solid" if comp >= 0.1 else
             "weak" if comp <= -0.4 else "soft" if comp <= -0.1 else "neutral")
    return Fundamentals(
        ticker=ov.get("Symbol", ""), pe=pe, peg=peg, pb=pb, roe=roe,
        profit_margin=pm, rev_growth_yoy=rg, eps_growth_yoy=eg,
        value_score=value, quality_score=quality, growth_score=growth,
        fundamental_score=comp, label=label)


def fetch_overview(ticker: str, timeout: int = 30) -> dict:
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not key:
        return {}
    url = f"{_AV}?function=OVERVIEW&symbol={ticker}&apikey={key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-trader"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        return d if d.get("Symbol") else {}
    except Exception:  # noqa: BLE001
        return {}


def get_fundamentals(ticker: str, max_age_days: int = 14, conn=None):
    """Cached fundamentals for a ticker. Returns Fundamentals or None.

    Cache lives in CRSP-lite enrichment (key='av_overview'); refreshed when
    older than max_age_days. Falls back to cache on a fetch miss/limit.
    """
    ticker = ticker.upper()
    own = False
    try:
        from .crsp.schema import connect, now_iso
        if conn is None:
            conn, own = connect(), True
        row = conn.execute(
            "SELECT s.permno, e.value, e.created_at FROM securities s "
            "LEFT JOIN enrichment e ON e.permno=s.permno AND e.key='av_overview' "
            "WHERE s.ticker=? ORDER BY e.created_at DESC LIMIT 1", (ticker,)).fetchone()
        permno = row["permno"] if row else None
        cached_ov = None
        if row and row["value"] and row["created_at"]:
            age = (datetime.now(timezone.utc) -
                   datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days
            if age <= max_age_days:
                cached_ov = json.loads(row["value"])
        if cached_ov:
            return score_overview(cached_ov)
        ov = fetch_overview(ticker)
        if not ov:
            # fetch failed/limited: use stale cache if any
            if row and row["value"]:
                return score_overview(json.loads(row["value"]))
            return None
        if permno is not None:
            conn.execute(
                "INSERT INTO enrichment(permno,key,value,confidence,model,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (permno, "av_overview", json.dumps(ov), 1.0, "alphavantage", now_iso()))
            conn.commit()
        return score_overview(ov)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if own and conn is not None:
            conn.close()


if __name__ == "__main__":
    # offline pure-scoring demo (no key needed)
    cheap_quality = {"Symbol": "DEMOA", "PERatio": "9", "PEGRatio": "0.8",
                     "PriceToBookRatio": "1.5", "ReturnOnEquityTTM": "0.28",
                     "ProfitMargin": "0.22", "QuarterlyRevenueGrowthYOY": "0.18",
                     "QuarterlyEarningsGrowthYOY": "0.25"}
    pricey_weak = {"Symbol": "DEMOB", "PERatio": "55", "PEGRatio": "3.2",
                   "PriceToBookRatio": "12", "ReturnOnEquityTTM": "0.02",
                   "ProfitMargin": "0.01", "QuarterlyRevenueGrowthYOY": "-0.08",
                   "QuarterlyEarningsGrowthYOY": "-0.2"}
    for ov in (cheap_quality, pricey_weak):
        f = score_overview(ov)
        print(f"{f.ticker}: comp={f.fundamental_score:+.2f} {f.label:8s} "
              f"(value={f.value_score:+.2f} quality={f.quality_score:+.2f} growth={f.growth_score:+.2f})")
    import os as _os
    if _os.environ.get("ALPHAVANTAGE_API_KEY"):
        print("AAPL live:", get_fundamentals("AAPL"))
