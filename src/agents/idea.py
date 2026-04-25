from __future__ import annotations

from src.types import Node, ToolBundle


def run(event: str, *, tools: ToolBundle, model: str) -> list[Node]:
    """Generate 3 to 8 candidate first order Nodes from a plain English event."""
    raise NotImplementedError
