from __future__ import annotations

from pathlib import Path

import pytest

from src.types import CausalGraph, Edge, Node
from src.viz.graph import to_networkx


def _node(nid: str, layer: int = 0, label: str | None = None) -> Node:
    return Node(id=nid, label=label or nid, description=f"{nid} desc", layer=layer)


def test_to_networkx_preserves_nodes_and_edges():
    g = CausalGraph(
        nodes={"a": _node("a", 0), "b": _node("b", 1, "B label")},
        edges=[Edge(src="a", dst="b", mechanism="m", sensitivity=0.6, confidence=0.7, id="e1")],
        root="a",
    )
    nx_g = to_networkx(g)

    assert set(nx_g.nodes()) == {"a", "b"}
    assert nx_g.nodes["b"]["label"] == "B label"
    assert nx_g.nodes["b"]["layer"] == 1
    assert ("a", "b") in nx_g.edges()
    assert nx_g.edges["a", "b"]["mechanism"] == "m"
    assert nx_g.edges["a", "b"]["sensitivity"] == 0.6
    assert nx_g.edges["a", "b"]["edge_id"] == "e1"


def test_to_networkx_handles_empty_graph():
    nx_g = to_networkx(CausalGraph())
    assert nx_g.number_of_nodes() == 0
    assert nx_g.number_of_edges() == 0


def test_render_pyvis_creates_html_file(tmp_path):
    pytest.importorskip("pyvis")
    from src.viz.graph import render_pyvis

    g = CausalGraph(
        nodes={"a": _node("a", 0), "b": _node("b", 1)},
        edges=[Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.5)],
        root="a",
    )
    out = render_pyvis(g, tmp_path / "graph.html")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "<html" in content.lower()
    # Node labels should appear somewhere in the rendered HTML.
    assert "a" in content
