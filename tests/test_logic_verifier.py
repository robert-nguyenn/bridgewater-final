from __future__ import annotations

from unittest.mock import MagicMock

from src.agents import logic_verifier
from src.agents.logic_verifier import (
    CHAIN_FAILURE_CATEGORIES,
    ChainVerification,
    _parse,
    run,
    verify_paths,
)
from src.types import CausalGraph, Edge, Node


def _node(nid: str, layer: int = 1) -> Node:
    return Node(id=nid, label=nid, description=f"{nid} desc", layer=layer)


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


def test_module_has_run_and_verify_paths():
    assert hasattr(logic_verifier, "run")
    assert hasattr(logic_verifier, "verify_paths")


def test_chain_failure_categories_exact_set():
    assert CHAIN_FAILURE_CATEGORIES == {
        "sign_inconsistency",
        "magnitude_leap",
        "equivocation",
        "time_mismatch",
        "missing_step",
    }


def test_empty_chain_passes_trivially():
    result = run([], nodes={})
    assert result.ok is True
    assert "empty" in result.reason.lower()


def test_single_edge_chain_passes_trivially():
    edge = Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.5)
    result = run([edge], nodes={"a": _node("a"), "b": _node("b")})
    assert result.ok is True
    assert "single" in result.reason.lower()


def test_parse_pass():
    text = '```json\n{"ok": true, "reason": "coherent", "failed_edge_idx": null, "failure_category": null}\n```'
    v = _parse(text)
    assert v.ok is True
    assert v.failed_edge_idx is None


def test_parse_fail_with_known_category():
    text = '```json\n{"ok": false, "reason": "magnitude jumps", "failed_edge_idx": 1, "failure_category": "magnitude_leap"}\n```'
    v = _parse(text)
    assert v.ok is False
    assert v.failure_category == "magnitude_leap"
    assert v.failed_edge_idx == 1


def test_parse_drops_unknown_category():
    text = '```json\n{"ok": false, "failure_category": "made_up", "failed_edge_idx": 0}\n```'
    v = _parse(text)
    assert v.failure_category is None


def test_parse_unparseable():
    v = _parse("not json")
    assert v.ok is False
    assert "parse" in v.reason.lower()


def test_run_with_mocked_client():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _msg(
        '```json\n{"ok": false, "reason": "magnitude leap at step 1", "failed_edge_idx": 1, "failure_category": "magnitude_leap"}\n```'
    )
    nodes = {"a": _node("a", 0), "b": _node("b", 1), "c": _node("c", 2)}
    chain = [
        Edge(src="a", dst="b", mechanism="m1", sensitivity=0.3, confidence=0.4),
        Edge(src="b", dst="c", mechanism="m2", sensitivity=0.9, confidence=0.5),
    ]
    result = run(chain, nodes=nodes, client=fake_client)
    assert result.ok is False
    assert result.failure_category == "magnitude_leap"
    fake_client.messages.create.assert_called_once()


def test_verify_paths_empty_graph():
    assert verify_paths(CausalGraph()) == {}


def test_verify_paths_skips_short_paths():
    fake_client = MagicMock()
    nodes = {"root": _node("root", 0), "a": _node("a", 1)}
    graph = CausalGraph(
        nodes=nodes,
        edges=[Edge(src="root", dst="a", mechanism="m", sensitivity=0.5, confidence=0.5, id="e1")],
        root="root",
    )
    results = verify_paths(graph, client=fake_client, min_path_length=2)
    # Only one path of length 1 exists; min_path_length=2 should skip it.
    assert results == {}
    fake_client.messages.create.assert_not_called()


def test_verify_paths_runs_on_long_chain():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _msg(
        '```json\n{"ok": true, "reason": "fine", "failed_edge_idx": null, "failure_category": null}\n```'
    )
    nodes = {
        "root": _node("root", 0),
        "a": _node("a", 1),
        "b": _node("b", 2),
        "c": _node("c", 3),
    }
    edges = [
        Edge(src="root", dst="a", mechanism="m1", sensitivity=0.5, confidence=0.5, id="e1"),
        Edge(src="a", dst="b", mechanism="m2", sensitivity=0.5, confidence=0.5, id="e2"),
        Edge(src="b", dst="c", mechanism="m3", sensitivity=0.5, confidence=0.5, id="e3"),
    ]
    graph = CausalGraph(nodes=nodes, edges=edges, root="root")
    results = verify_paths(graph, client=fake_client, min_path_length=2, max_workers=2)
    assert len(results) == 1
    assert all(r.ok for r in results.values())


def test_verify_paths_picks_longest_paths_first():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _msg(
        '```json\n{"ok": true, "reason": "x"}\n```'
    )
    # Build a graph with multiple paths of different lengths.
    nodes = {f"n{i}": _node(f"n{i}", layer=i) for i in range(5)}
    nodes["root"] = _node("root", 0)
    edges = [
        Edge(src="root", dst="n1", mechanism="m", sensitivity=0.5, confidence=0.5, id="e1"),
        Edge(src="n1", dst="n2", mechanism="m", sensitivity=0.5, confidence=0.5, id="e2"),
        Edge(src="n2", dst="n3", mechanism="m", sensitivity=0.5, confidence=0.5, id="e3"),
        Edge(src="root", dst="n4", mechanism="m", sensitivity=0.5, confidence=0.5, id="e4"),  # short path
    ]
    graph = CausalGraph(nodes=nodes, edges=edges, root="root")
    results = verify_paths(graph, client=fake_client, min_path_length=2, max_paths=10)
    # Long path (root -> n1 -> n2 -> n3) yields 3 edges; short root -> n4 has 1 edge so skipped.
    keys = list(results.keys())
    assert any("e1->e2->e3" in k for k in keys)
    assert not any(k == "e4" for k in keys)
