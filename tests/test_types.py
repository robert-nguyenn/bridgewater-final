from __future__ import annotations

from src.types import CausalGraph, Edge, Evidence, Node


def test_node_constructs():
    n = Node(id="n1", label="USD strengthens", description="x", layer=1)
    assert n.id == "n1"
    assert n.evidence == []


def test_edge_constructs():
    e = Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.5)
    assert 0 <= e.sensitivity <= 1


def test_graph_default_empty():
    g = CausalGraph()
    assert g.nodes == {}
    assert g.edges == []


def test_evidence_kind():
    ev = Evidence(kind="fred_series", ref="CPIAUCSL")
    assert ev.kind == "fred_series"
