from __future__ import annotations

import pytest

from src.tools import yahoo


def test_yahoo_module_imports():
    assert hasattr(yahoo, "yahoo_fundamentals")
    assert hasattr(yahoo, "yahoo_prices")


@pytest.mark.skip(reason="implement once yahoo_prices is wired")
def test_yahoo_prices_smoke():
    pass
