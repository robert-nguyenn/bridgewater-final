from __future__ import annotations

import os

import pytest

from src.tools import yahoo


def test_yahoo_module_imports():
    assert hasattr(yahoo, "yahoo_fundamentals")
    assert hasattr(yahoo, "yahoo_prices")


@pytest.mark.skipif(
    os.getenv("RUN_LIVE") != "1",
    reason="live Yahoo test, set RUN_LIVE=1 to enable",
)
def test_yahoo_prices_live_smoke():
    from datetime import date

    df = yahoo.yahoo_prices("SPY", date(2024, 1, 2), date(2024, 1, 10))
    # Either a DataFrame or a ToolError is acceptable depending on rate limits.
    assert df is not None
