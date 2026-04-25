from __future__ import annotations

import pytest

from src.agents import macro_comparator


def test_macro_comparator_module_has_run():
    assert hasattr(macro_comparator, "run")


@pytest.mark.skip(reason="implement once MacroComparatorAgent.run is wired")
def test_macro_comparator_smoke():
    pass
