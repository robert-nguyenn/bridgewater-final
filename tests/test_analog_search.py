from __future__ import annotations

import pytest

from src.agents import analog_search


def test_analog_search_module_has_run():
    assert hasattr(analog_search, "run")


@pytest.mark.skip(reason="implement once AnalogSearchAgent.run is wired")
def test_analog_search_smoke():
    pass
