from __future__ import annotations

import pytest

from src.agents import pruner


def test_pruner_module_has_run():
    assert hasattr(pruner, "run")


@pytest.mark.skip(reason="implement once PrunerAgent.run is wired")
def test_pruner_smoke():
    pass
