from __future__ import annotations

from src.types import CausalGraph, Node, ToolBundle


def run(seed: Node, *, tools: ToolBundle, model: str, depth: int = 3) -> CausalGraph:
    """Build a 2 to 3 layer causal subtree from a seed Node."""
    raise NotImplementedError
