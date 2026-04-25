from __future__ import annotations

from dataclasses import dataclass

from src.types import Node, ToolBundle


@dataclass
class PortfolioImpact:
    asset_class: str
    direction: str  # "up" | "down" | "mixed"
    summary: str
    contributing_nodes: list[str]


def run(terminals: list[Node], *, tools: ToolBundle, model: str) -> list[PortfolioImpact]:
    """Map terminal nodes to per asset class impact summaries."""
    raise NotImplementedError
