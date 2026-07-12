"""The router autoloader mounts dashboard/api/* and the quote/panels routes exist.

Dependency-free: inspects the app's route table and calls handlers directly, so
it needs no HTTP test client (httpx) — keeping the suite runnable anywhere.
"""
from pathlib import Path

from fastapi import FastAPI

from dashboard.app import app, list_panels
from dashboard.api._autoload import include_all
from dashboard.api import quotes as quotes_api


def _paths(a):
    # Use the OpenAPI schema: this FastAPI version mounts routers behind lazy
    # proxies, so the schema (not app.routes) is the reliable source of truth.
    return set(a.openapi().get("paths", {}).keys())


def test_quote_and_panel_routes_registered():
    paths = _paths(app)
    for p in ("/api/quotes", "/api/quotes/status", "/api/quotes/stream", "/api/panels"):
        assert p in paths, f"{p} not mounted"


def test_autoloader_mounts_quotes_on_fresh_app():
    a = FastAPI()
    mounted = include_all(a)
    assert "quotes" in mounted
    assert "/api/quotes" in _paths(a)


def test_panels_handler_shape():
    body = list_panels()
    assert "panels" in body and isinstance(body["panels"], list)
    assert all(p.startswith("/static/js/panels/") and p.endswith(".js")
               for p in body["panels"])


def test_quotes_snapshot_handler_keyless():
    body = quotes_api.quotes_snapshot("")
    assert "quotes" in body and "version" in body
    assert isinstance(body["quotes"], dict)


def test_terminal_js_present_and_has_registry():
    js = Path("dashboard/static/js/terminal.js")
    assert js.exists()
    text = js.read_text()
    assert "registerPanel" in text and "registerCommand" in text
