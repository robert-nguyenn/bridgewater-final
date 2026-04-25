from __future__ import annotations

import pytest

from src.agents import tree_builder


def test_tree_builder_module_has_run():
    assert hasattr(tree_builder, "run")


@pytest.mark.skip(reason="implement once TreeBuilderAgent.run is wired")
def test_tree_builder_smoke():
    pass
