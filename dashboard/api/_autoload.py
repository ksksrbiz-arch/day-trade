"""Discover and mount every ``router`` in the ``dashboard.api`` package."""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

_PKG = "dashboard.api"


def include_all(app) -> list[str]:
    """Import each ``dashboard/api/*.py`` module and include its ``router``.

    Returns the list of module names that were mounted. Import/attribute errors
    on one module are logged and skipped so a single bad panel can't take down
    the whole API. Modules whose name starts with ``_`` are ignored.
    """
    mounted: list[str] = []
    pkg_dir = Path(__file__).resolve().parent
    for info in pkgutil.iter_modules([str(pkg_dir)]):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{_PKG}.{info.name}")
            router = getattr(mod, "router", None)
            if router is None:
                continue
            app.include_router(router)
            mounted.append(info.name)
        except Exception as e:  # noqa: BLE001
            print(f"[api-autoload] skipped {info.name}: {e}")
    return mounted
