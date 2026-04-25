from __future__ import annotations

import pytest

from src.agents import defender


def test_defender_module_has_run():
    assert hasattr(defender, "run")


@pytest.mark.skip(reason="implement once DefenderAgent.run is wired")
def test_defender_smoke():
    pass
