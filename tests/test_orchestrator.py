from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.orchestrator import (
    Debate,
    PipelineResult,
    run_adversarial_debate,
    run_pipeline,
)
from src.types import CausalGraph, Edge, Node


def _node(nid: str, label: str, layer: int = 1) -> Node:
    return Node(id=nid, label=label, description=f"{label} description", layer=layer)


def _edge(src: str, dst: str, eid: str) -> Edge:
    return Edge(src=src, dst=dst, mechanism="m", sensitivity=0.5, confidence=0.5, id=eid)


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


def test_orchestrator_dry_run_returns_pipeline_result():
    result = run_pipeline("test event", dry_run=True)
    assert isinstance(result, PipelineResult)
    assert isinstance(result.graph, CausalGraph)
    assert result.graph.nodes == {}


def test_debate_survives_property():
    adv = Critique(target_id="x", counterargument="x", score=0.4)
    df = Rebuttal(target_id="x", rebuttal="y", score=0.6)
    d = Debate(target_id="x", critique=adv, rebuttal=df)
    assert d.survives is True
    assert abs(d.margin - 0.2) < 1e-9


def test_debate_loses_when_adversary_outscores():
    adv = Critique(target_id="x", counterargument="x", score=0.7)
    df = Rebuttal(target_id="x", rebuttal="y", score=0.5)
    d = Debate(target_id="x", critique=adv, rebuttal=df)
    assert d.survives is False
    assert d.margin < 0


def test_debate_ties_go_to_defender():
    adv = Critique(target_id="x", counterargument="x", score=0.5)
    df = Rebuttal(target_id="x", rebuttal="y", score=0.5)
    d = Debate(target_id="x", critique=adv, rebuttal=df)
    assert d.survives is True


def test_run_adversarial_debate_empty_graph():
    assert run_adversarial_debate(CausalGraph()) == {}


def test_run_adversarial_debate_calls_both_agents_per_edge():
    """Without the moderator, two LLM calls per edge (adversary + defender)."""
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    edges = [_edge("a", "b", "e_1"), _edge("a", "b", "e_2")]
    graph = CausalGraph(nodes=nodes, edges=edges, root="a")

    crit = _msg('```json\n{"target_id": "x", "counterargument": "weak", "score": 0.3}\n```')
    reb = _msg('```json\n{"target_id": "x", "rebuttal": "strong", "score": 0.7}\n```')
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [crit, reb, crit, reb]

    debates = run_adversarial_debate(graph, client=fake_client, use_moderator=False)

    assert set(debates.keys()) == {"e_1", "e_2"}
    assert all(d.survives for d in debates.values())
    assert fake_client.messages.create.call_count == 4
    assert all(d.verdict is None for d in debates.values())


def test_run_adversarial_debate_with_moderator():
    """With the moderator, three LLM calls per edge (adversary + defender + moderator)."""
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    edges = [_edge("a", "b", "e_1")]
    graph = CausalGraph(nodes=nodes, edges=edges, root="a")

    crit = _msg('```json\n{"target_id": "x", "counterargument": "weak", "score": 0.3}\n```')
    reb = _msg('```json\n{"target_id": "x", "rebuttal": "strong", "score": 0.7}\n```')
    mod = _msg('```json\n{"target_id": "e_1", "decision": "drop", "confidence_adjustment": -0.1, "reasoning": "adversary cited specifics"}\n```')
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [crit, reb, mod]

    debates = run_adversarial_debate(graph, client=fake_client, use_moderator=True)

    assert fake_client.messages.create.call_count == 3
    d = debates["e_1"]
    assert d.verdict is not None
    assert d.verdict.decision == "drop"
    # Moderator decision overrides score margin: defender outscored adversary
    # but moderator says drop, so survives = False.
    assert d.survives is False


def test_run_adversarial_debate_includes_nodes_when_requested():
    nodes = {"a": _node("a", "A")}
    graph = CausalGraph(nodes=nodes, edges=[], root="a")

    crit = _msg('```json\n{"target_id": "a", "counterargument": "x", "score": 0.6}\n```')
    reb = _msg('```json\n{"target_id": "a", "rebuttal": "y", "score": 0.4}\n```')
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [crit, reb]

    debates = run_adversarial_debate(
        graph, include_nodes=True, client=fake_client, use_moderator=False
    )
    assert "a" in debates
    assert debates["a"].survives is False
    assert abs(debates["a"].margin - (-0.2)) < 1e-9


def test_run_adversarial_debate_uses_edge_id_as_key():
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    edge = Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.5, id="custom_id")
    graph = CausalGraph(nodes=nodes, edges=[edge], root="a")

    crit = _msg('```json\n{"target_id": "x", "counterargument": "x", "score": 0.4}\n```')
    reb = _msg('```json\n{"target_id": "x", "rebuttal": "y", "score": 0.5}\n```')
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [crit, reb]

    debates = run_adversarial_debate(graph, client=fake_client, use_moderator=False)
    assert "custom_id" in debates
