from __future__ import annotations

import pytest

from src.agents import adversary


def test_adversary_module_has_run():
    assert hasattr(adversary, "run")


@pytest.mark.skip(reason="implement once AdversaryAgent.run is wired")
def test_adversary_smoke():
    pass
