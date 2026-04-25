from __future__ import annotations

import pytest

from src.agents import idea


def test_idea_module_has_run():
    assert hasattr(idea, "run")


@pytest.mark.skip(reason="implement once IdeaAgent.run is wired")
def test_idea_smoke():
    pass
