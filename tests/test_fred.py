from __future__ import annotations

from src.tools import fred


def test_fred_module_imports():
    assert hasattr(fred, "fred_get_series")
    assert hasattr(fred, "fred_find_extrema")
    assert hasattr(fred, "macro_snapshot")


# Live smoke tests (real FRED API) live in tests/test_fred_live.py and are
# auto-skipped when FRED_API_KEY is missing.
