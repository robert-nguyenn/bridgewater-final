from __future__ import annotations

from src.types import Episode, Node, ToolBundle


def run(node: Node, *, tools: ToolBundle, model: str) -> list[Episode]:
    """For a first order Node, scan FRED for past episodes of comparable movement."""
    raise NotImplementedError
