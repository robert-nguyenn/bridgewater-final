from __future__ import annotations

import pytest

from src.tools import fred


def test_fred_module_imports():
    assert hasattr(fred, "fred_get_series")
    assert hasattr(fred, "fred_find_extrema")
    assert hasattr(fred, "macro_snapshot")


@pytest.mark.skip(reason="implement once fred_get_series is wired")
def test_fred_get_series_smoke():
    pass
