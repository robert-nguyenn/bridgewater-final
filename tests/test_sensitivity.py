from __future__ import annotations

import pytest

from src.agents import sensitivity


def test_sensitivity_module_has_run():
    assert hasattr(sensitivity, "run")


@pytest.mark.skip(reason="implement once SensitivityAgent.run is wired")
def test_sensitivity_smoke():
    pass
