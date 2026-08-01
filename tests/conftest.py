"""Shared test fixtures.

The collectors are thin parsers wrapped around system commands and files.
Tests exercise the parsers against recorded fixture output so they run
anywhere — no privileged container, no real hardware, no cluster.
"""

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    """Read a recorded command output fixture by filename."""

    def _read(name: str) -> str:
        return (FIXTURE_DIR / name).read_text()

    return _read


@pytest.fixture
def clear_caches():
    """Drop every collector's ttl_cache so tests don't leak state into each other."""
    import importlib
    import pkgutil

    import app.collectors

    def _clear():
        for mod in pkgutil.iter_modules(app.collectors.__path__):
            module = importlib.import_module(f"app.collectors.{mod.name}")
            for attr in vars(module).values():
                clear = getattr(attr, "cache_clear", None)
                if callable(clear):
                    clear()

    _clear()
    yield _clear
    _clear()
