"""Economic / earnings calendar endpoint (terminal ``ECO`` panel).

There is no dedicated calendar data source in the platform, so this is a
*lightweight, honest* adapter:

* **earnings** — derived from :mod:`trader.fundamentals` *only if* it exposes a
  next-report / earnings-date field. It does not (the OVERVIEW model carries
  growth ratios, no dates), so we return ``[]`` rather than fabricate dates.
* **econ** — a small curated set of recurring US macro releases (NFP, CPI, PPI,
  Retail Sales, PCE) whose upcoming dates are *computed* from the calendar, plus
  the *scheduled* FOMC announcement days. Every event carries a ``source`` field
  so derived/estimated data is clearly labelled and never mistaken for a live
  feed.

KEYLESS-SAFE: no network, no keys, no exceptions escape — worst case is
``{"earnings": [], "econ": []}``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# FOMC scheduled announcement days (2nd day of each two-day meeting). These are
# published by the Federal Reserve ahead of time, so they are "scheduled", not
# estimated. Extend as new years are announced.
_FOMC_DATES = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-16",
    "2027-01-27", "2027-03-17",
]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th (1-based) ``weekday`` (Mon=0..Sun=6) of ``year``/``month``."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


# Recurring monthly macro releases. Each rule computes an *estimated* date from a
# month; real release dates vary by a day or two, hence source="recurring-estimate".
_MONTHLY = [
    ("Nonfarm Payrolls (NFP)", lambda y, m: _nth_weekday(y, m, 4, 1), "8:30 ET"),
    ("CPI (Consumer Price Index)", lambda y, m: _nth_weekday(y, m, 2, 2), "8:30 ET"),
    ("PPI (Producer Price Index)", lambda y, m: _nth_weekday(y, m, 3, 2), "8:30 ET"),
    ("Retail Sales", lambda y, m: _nth_weekday(y, m, 1, 3), "8:30 ET"),
    ("PCE (Core Inflation)", lambda y, m: _nth_weekday(y, m, 4, 4), "8:30 ET"),
]


def _upcoming_econ(today: date, horizon_days: int = 75) -> list[dict]:
    """Build the upcoming econ-event list within ``horizon_days`` of ``today``."""
    end = today + timedelta(days=horizon_days)
    out: list[dict] = []

    # Rule-computed monthly releases across this month and the next few.
    y, m = today.year, today.month
    for _ in range(4):  # current month + 3 ahead comfortably covers the horizon
        for name, rule, when in _MONTHLY:
            try:
                d = rule(y, m)
            except Exception:  # noqa: BLE001
                continue
            if today <= d <= end:
                out.append({
                    "name": name, "date": d.isoformat(), "time": when,
                    "category": "macro", "source": "recurring-estimate",
                })
        m += 1
        if m > 12:
            m, y = 1, y + 1

    # Scheduled FOMC announcements.
    for s in _FOMC_DATES:
        try:
            d = date.fromisoformat(s)
        except ValueError:
            continue
        if today <= d <= end:
            out.append({
                "name": "FOMC Rate Decision", "date": d.isoformat(),
                "time": "14:00 ET", "category": "fed", "source": "scheduled",
            })

    out.sort(key=lambda e: (e["date"], e["name"]))
    return out


def _upcoming_earnings() -> list[dict]:
    """Earnings dates from fundamentals, IF it exposes a report-date field.

    It currently does not, so this honestly returns ``[]`` instead of inventing
    dates. Guarded so an import/attribute change can never crash the endpoint.
    """
    try:
        from trader import fundamentals  # noqa: F401
        # No next-report / earnings-date attribute exists on the Fundamentals
        # model or module today; nothing to derive. Return empty rather than
        # fabricate. (Wire real dates here if such a field is ever added.)
        return []
    except Exception:  # noqa: BLE001
        return []


def get_calendar() -> dict:
    """Assemble the calendar payload. Never raises."""
    try:
        today = datetime.now(timezone.utc).date()
        econ = _upcoming_econ(today)
        earnings = _upcoming_earnings()
    except Exception:  # noqa: BLE001
        return {"earnings": [], "econ": [], "asof": None,
                "note": "calendar unavailable"}
    return {
        "earnings": earnings,
        "econ": econ,
        "asof": today.isoformat(),
        "note": ("econ dates are rule-computed estimates (source=recurring-estimate) "
                 "except FOMC (scheduled); earnings need a report-date source."),
    }


@router.get("")
def calendar():
    """Upcoming earnings + economic events for the terminal ECO panel."""
    return get_calendar()
