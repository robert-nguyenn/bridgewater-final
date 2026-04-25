from __future__ import annotations

import os

import pytest

from src.tools import hf


def test_hf_module_imports():
    assert hasattr(hf, "hf_dataset_query")
    assert hasattr(hf, "central_bank_search")
    assert hasattr(hf, "news_search")


def test_default_repos_are_set():
    assert hf.DEFAULT_CENTRAL_BANK_REPO
    assert hf.DEFAULT_NEWS_REPO


@pytest.mark.skipif(
    os.getenv("RUN_LIVE") != "1",
    reason="live HF test, set RUN_LIVE=1 (requires HF_TOKEN) to enable",
)
def test_hf_dataset_query_live_smoke():
    out = hf.hf_dataset_query(
        hf.DEFAULT_CENTRAL_BANK_REPO,
        {"field": "text", "contains": "inflation", "limit": 2},
    )
    # Either rows or a ToolError is acceptable.
    assert out is not None
