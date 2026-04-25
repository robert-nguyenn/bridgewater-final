from __future__ import annotations

import pytest

from src.agents import scenario


def test_scenario_module_has_run():
    assert hasattr(scenario, "run")


@pytest.mark.skip(reason="implement once ScenarioAgent.run is wired")
def test_scenario_smoke():
    pass
