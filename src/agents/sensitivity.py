from __future__ import annotations

from src.types import Edge, ToolBundle


def run(edge: Edge, *, tools: ToolBundle, model: str) -> Edge:
    """Score sensitivity and confidence for an Edge with cited supporting data."""
    raise NotImplementedError
