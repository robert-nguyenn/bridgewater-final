from __future__ import annotations

from typing import Any, Union

from src.types import ToolError


def hf_dataset_query(
    repo_id: str, filter: dict[str, Any]
) -> Union[list[dict[str, Any]], ToolError]:
    """Generic accessor for HuggingFace datasets used in this project."""
    raise NotImplementedError


def central_bank_search(
    query: str, date_range: tuple[str, str]
) -> Union[list[dict[str, Any]], ToolError]:
    """Search aufklarer/central-bank-communications and istat-ai/ECB-FED-speeches."""
    raise NotImplementedError


def news_search(
    query: str, date_range: tuple[str, str]
) -> Union[list[dict[str, Any]], ToolError]:
    """Search ag_news and dell-research-harvard/newswire."""
    raise NotImplementedError
