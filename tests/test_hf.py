from __future__ import annotations

import pytest

from src.tools import hf


def test_hf_module_imports():
    assert hasattr(hf, "hf_dataset_query")
    assert hasattr(hf, "central_bank_search")
    assert hasattr(hf, "news_search")


@pytest.mark.skip(reason="implement once hf_dataset_query is wired")
def test_hf_dataset_query_smoke():
    pass
