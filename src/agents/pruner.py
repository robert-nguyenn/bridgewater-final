from __future__ import annotations

from typing import Any

from src.types import CausalGraph, ToolBundle


def run(
    graph: CausalGraph,
    *,
    debate: dict[str, Any],
    comparator: dict[str, Any],
    tools: ToolBundle,
    model: str,
) -> CausalGraph:
    """Prune nodes and edges that lose debates or fail comparator filters."""
    raise NotImplementedError
