from __future__ import annotations

from unittest.mock import MagicMock

from src.agents import adversary
from src.agents.adversary import (
    ATTACK_TYPES,
    _parse_critique,
)
from src.agents._common import format_target
from src.types import Edge, Evidence, Node


def _node(nid: str, label: str, layer: int = 1) -> Node:
    return Node(id=nid, label=label, description=f"{label} description", layer=layer)


def test_module_has_run():
    assert hasattr(adversary, "run")


def test_attack_types_match_prompt_enum():
    expected = {
        "counter_example",
        "structural_objection",
        "magnitude_doubt",
        "transmission_break",
        "regime_mismatch",
        "precondition_failure",
    }
    assert ATTACK_TYPES == expected


def test_format_target_node():
    text = format_target(_node("a", "USD strengthens"))
    assert "USD strengthens" in text
    assert "'a'" in text


def test_format_target_edge_with_nodes_and_evidence():
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    edge = Edge(
        src="a", dst="b", mechanism="cost pass-through",
        sensitivity=0.6, confidence=0.7,
        supporting_data=[Evidence(kind="fred_series", ref="CPIAUCSL", note="Y/Y")],
    )
    text = format_target(edge, nodes)
    assert "cost pass-through" in text
    assert "CPIAUCSL" in text
    assert "0.60" in text
    assert "Y/Y" in text


def test_format_target_edge_marks_missing_evidence():
    edge = Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.5)
    assert "none cited" in format_target(edge, {})


def test_parse_critique_full_payload():
    text = """```json
{
  "target_id": "n1",
  "attack_type": "counter_example",
  "counterargument": "The 2018 tariffs strengthened USD, contrary to the claim.",
  "cited_evidence": ["FRED:DTWEXBGS", "episode:2018 Section 301"],
  "score": 0.7
}
```"""
    c = _parse_critique(text, "fallback")
    assert c.target_id == "n1"
    assert c.attack_type == "counter_example"
    assert c.score == 0.7
    assert c.cited_evidence == ["FRED:DTWEXBGS", "episode:2018 Section 301"]


def test_parse_critique_drops_unknown_attack_type():
    text = '```json\n{"target_id": "n1", "attack_type": "made_up", "counterargument": "x", "score": 0.5}\n```'
    assert _parse_critique(text, "fb").attack_type is None


def test_parse_critique_clamps_score_high_and_low():
    high = '```json\n{"target_id": "n1", "counterargument": "x", "score": 1.5}\n```'
    assert _parse_critique(high, "fb").score == 1.0

    low = '```json\n{"target_id": "n1", "counterargument": "x", "score": -0.3}\n```'
    assert _parse_critique(low, "fb").score == 0.0


def test_parse_critique_handles_unparseable():
    c = _parse_critique("not json", "fallback")
    assert c.target_id == "fallback"
    assert c.score == 0.0
    assert "parser failed" in c.counterargument.lower()


def test_run_with_node_target():
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(
        text='```json\n{"target_id": "n1", "attack_type": "structural_objection", '
             '"counterargument": "channel was severed", "score": 0.6}\n```'
    )]
    fake_client.messages.create.return_value = fake_msg

    result = adversary.run(_node("n1", "X"), client=fake_client)
    assert result.target_id == "n1"
    assert result.attack_type == "structural_objection"
    assert result.score == 0.6
    fake_client.messages.create.assert_called_once()


def test_run_with_edge_target_uses_fallback_id():
    fake_client = MagicMock()
    fake_msg = MagicMock()
    # Response omits target_id; agent should fall back to edge.id.
    fake_msg.content = [MagicMock(
        text='```json\n{"counterargument": "weak", "score": 0.3}\n```'
    )]
    fake_client.messages.create.return_value = fake_msg

    edge = Edge(src="a", dst="b", mechanism="m", sensitivity=0.4, confidence=0.4, id="e_test1")
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}

    result = adversary.run(edge, nodes=nodes, client=fake_client)
    assert result.target_id == "e_test1"
    assert result.score == 0.3
