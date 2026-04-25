from __future__ import annotations

import pytest

from src.agents import portfolio


def test_portfolio_module_has_run():
    assert hasattr(portfolio, "run")


@pytest.mark.skip(reason="implement once PortfolioAgent.run is wired")
def test_portfolio_smoke():
    pass
