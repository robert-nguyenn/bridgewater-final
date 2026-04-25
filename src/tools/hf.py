from __future__ import annotations

import logging
from typing import Any, Union

from src.tools.cache import disk_cache
from src.types import ToolError

logger = logging.getLogger(__name__)

DEFAULT_CENTRAL_BANK_REPO = "istat-ai/ECB-FED-speeches"
DEFAULT_NEWS_REPO = "dell-research-harvard/newswire"


def _filter_rows(rows, field: str, needle: str, limit: int) -> list[dict[str, Any]]:
    needle = (needle or "").lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            row = dict(row)
        if needle and field:
            value = str(row.get(field, "")).lower()
            if needle not in value:
                continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


@disk_cache("hf_dataset")
def hf_dataset_query(
    repo_id: str, filter: dict[str, Any]
) -> Union[list[dict[str, Any]], ToolError]:
    """Load a HuggingFace dataset and apply a simple substring filter.

    `filter` shape: {"field": str, "contains": str, "limit": int, "split": str}.
    Returns up to `limit` matching rows. Cached on disk."""
    try:
        from datasets import load_dataset
    except ImportError as exc:
        return ToolError(
            tool="hf",
            args={"repo_id": repo_id, "filter": filter},
            message=f"datasets not installed: {exc}",
        )

    split = filter.get("split", "train")
    field = filter.get("field", "text")
    needle = filter.get("contains", "")
    limit = int(filter.get("limit", 100))

    try:
        ds = load_dataset(repo_id, split=split, streaming=True)
    except Exception as exc:
        return ToolError(
            tool="hf",
            args={"repo_id": repo_id, "filter": filter},
            message=f"load_dataset failed: {exc}",
        )

    try:
        out = _filter_rows(ds, field, needle, limit)
    except Exception as exc:
        return ToolError(
            tool="hf",
            args={"repo_id": repo_id, "filter": filter},
            message=f"iteration failed: {exc}",
        )

    return out


def central_bank_search(
    query: str, date_range: tuple[str, str]
) -> Union[list[dict[str, Any]], ToolError]:
    """Search central bank speeches via the standard HF mirror."""
    return hf_dataset_query(
        DEFAULT_CENTRAL_BANK_REPO,
        {"field": "text", "contains": query, "limit": 50},
    )


def news_search(
    query: str, date_range: tuple[str, str]
) -> Union[list[dict[str, Any]], ToolError]:
    """Search financial news via the standard HF mirror."""
    return hf_dataset_query(
        DEFAULT_NEWS_REPO,
        {"field": "text", "contains": query, "limit": 50},
    )


__all__ = [
    "hf_dataset_query",
    "central_bank_search",
    "news_search",
    "DEFAULT_CENTRAL_BANK_REPO",
    "DEFAULT_NEWS_REPO",
]
