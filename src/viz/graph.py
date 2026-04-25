from __future__ import annotations

from pathlib import Path

from src.types import CausalGraph


def to_networkx(graph: CausalGraph):
    """Convert a CausalGraph into a networkx.DiGraph."""
    raise NotImplementedError


def render_pyvis(graph: CausalGraph, out_path: Path) -> Path:
    """Render the graph to an interactive pyvis HTML file."""
    raise NotImplementedError


def render_graphviz(graph: CausalGraph, out_path: Path) -> Path:
    """Render the graph to a static graphviz file."""
    raise NotImplementedError
