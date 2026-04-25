from __future__ import annotations

from src.agents import pruner
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.orchestrator import Debate
from src.types import CausalGraph, Edge, Node


def _node(nid: str, layer: int = 1) -> Node:
    return Node(id=nid, label=nid, description=nid, layer=layer)


def _edge(src: str, dst: str, eid: str) -> Edge:
    return Edge(src=src, dst=dst, mechanism="m", sensitivity=0.5, confidence=0.5, id=eid)


def _debate(eid: str, adv_score: float, def_score: float) -> Debate:
    return Debate(
        target_id=eid,
        critique=Critique(target_id=eid, counterargument="x", score=adv_score),
        rebuttal=Rebuttal(target_id=eid, rebuttal="y", score=def_score),
    )


def test_module_has_run():
    assert hasattr(pruner, "run")


def test_empty_graph_returns_empty_graph():
    result = pruner.run(CausalGraph())
    assert result.nodes == {} and result.edges == []


def test_no_debates_keeps_all_edges():
    g = CausalGraph(
        nodes={"a": _node("a", 0), "b": _node("b")},
        edges=[_edge("a", "b", "e1")],
        root="a",
    )
    result = pruner.run(g)
    assert len(result.edges) == 1


def test_drops_edge_where_adversary_outscores_defender():
    g = CausalGraph(
        nodes={"a": _node("a", 0), "b": _node("b"), "c": _node("c")},
        edges=[_edge("a", "b", "e1"), _edge("a", "c", "e2")],
        root="a",
    )
    debates = {"e1": _debate("e1", adv_score=0.8, def_score=0.3)}
    result = pruner.run(g, debates=debates)
    assert {e.id for e in result.edges} == {"e2"}
    # b is no longer reachable, gc'd
    assert "b" not in result.nodes
    assert "c" in result.nodes


def test_keeps_edge_where_defender_wins_tie():
    g = CausalGraph(
        nodes={"a": _node("a", 0), "b": _node("b")},
        edges=[_edge("a", "b", "e1")],
        root="a",
    )
    debates = {"e1": _debate("e1", adv_score=0.5, def_score=0.5)}
    result = pruner.run(g, debates=debates)
    assert len(result.edges) == 1


def test_drops_low_similarity_subtree():
    g = CausalGraph(
        nodes={
            "root": _node("root", 0),
            "fo": _node("fo", 1),
            "cs_root": _node("cs_root", 2),
            "cs_leaf": _node("cs_leaf", 3),
            "other_fo": _node("other_fo", 1),
        },
        edges=[
            _edge("root", "fo", "e1"),
            _edge("fo", "cs_root", "e2"),
            _edge("cs_root", "cs_leaf", "e3"),
            _edge("root", "other_fo", "e4"),
        ],
        root="root",
    )
    result = pruner.run(
        g,
        comparator={"bad_case": 0.1, "good_case": 0.5},
        case_study_subtree_roots={"bad_case": "cs_root"},
        similarity_threshold=0.3,
    )
    assert "cs_root" not in result.nodes
    assert "cs_leaf" not in result.nodes
    assert "other_fo" in result.nodes
    assert "fo" in result.nodes


def test_root_is_always_preserved():
    g = CausalGraph(
        nodes={"root": _node("root", 0), "b": _node("b")},
        edges=[_edge("root", "b", "e1")],
        root="root",
    )
    debates = {"e1": _debate("e1", adv_score=0.9, def_score=0.0)}
    result = pruner.run(g, debates=debates)
    assert result.nodes == {"root": g.nodes["root"]}
    assert result.root == "root"


def test_orphans_are_garbage_collected():
    """A node with no inbound edge from root is dropped after pruning."""
    g = CausalGraph(
        nodes={"root": _node("root", 0), "a": _node("a"), "orphan": _node("orphan")},
        edges=[_edge("root", "a", "e1")],
        root="root",
    )
    result = pruner.run(g)
    assert "orphan" not in result.nodes
    assert "a" in result.nodes


def test_high_similarity_subtree_is_kept():
    g = CausalGraph(
        nodes={
            "root": _node("root", 0),
            "fo": _node("fo", 1),
            "cs_root": _node("cs_root", 2),
        },
        edges=[_edge("root", "fo", "e1"), _edge("fo", "cs_root", "e2")],
        root="root",
    )
    result = pruner.run(
        g,
        comparator={"good": 0.9},
        case_study_subtree_roots={"good": "cs_root"},
        similarity_threshold=0.3,
    )
    assert "cs_root" in result.nodes
