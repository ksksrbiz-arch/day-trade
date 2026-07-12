"""
Auto-discovered FastAPI routers for the terminal.

Any module in this package that exposes a module-level ``router: APIRouter`` is
mounted automatically by :func:`_autoload.include_all` at app startup. This is
the seam that lets terminal panels be added as **new files only** — a new panel
ships its endpoint here and its UI under ``dashboard/static/js/panels/`` without
editing ``dashboard/app.py`` (see docs/ROADMAP.md, Phase 1).
"""
