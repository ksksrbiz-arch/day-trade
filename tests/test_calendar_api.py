"""Dependency-free tests for the calendar (ECO) terminal endpoint.

No TestClient/httpx: we assert the route is mounted via the OpenAPI schema and
call the handler directly.
"""
from dashboard.app import app


def test_route_mounted():
    assert "/api/calendar" in set(app.openapi()["paths"])


from dashboard.api.calendar_api import get_calendar, _nth_weekday
from datetime import date


def test_handler_shape():
    b = get_calendar()
    assert "earnings" in b and "econ" in b
    assert isinstance(b["earnings"], list)
    assert isinstance(b["econ"], list)


def test_econ_events_are_labelled_and_dated():
    b = get_calendar()
    for e in b["econ"]:
        assert "name" in e and "date" in e and "source" in e
        # date is a parseable ISO calendar date
        date.fromisoformat(e["date"])
        assert e["source"] in ("recurring-estimate", "scheduled")


def test_nth_weekday_first_friday():
    # First Friday of Jan 2027 is 2027-01-01 (a Friday); Fri=weekday 4.
    assert _nth_weekday(2027, 1, 4, 1) == date(2027, 1, 1)
    # Second Wednesday of Jul 2026 is 2026-07-08.
    assert _nth_weekday(2026, 7, 2, 2) == date(2026, 7, 8)
