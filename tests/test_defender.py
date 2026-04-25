from __future__ import annotations

from unittest.mock import MagicMock

from src.agents import defender
from src.agents.adversary import Critique
from src.agents.defender import (
    DEFENSE_TYPES,
    _format_critique_block,
    _parse_rebuttal,
)
from src.types import Edge, Evidence, Node


def _node(nid: str, label: str, layer: int = 1) -> Node:
    return Node(id=nid, label=label, description=f"{label} description", layer=layer)


def test_module_has_run():
    assert hasattr(defender, "run")


def test_defense_types_match_prompt_enum():
    expected = {
        "counter_evidence",
        "precondition_holds",
        "magnitude_robust",
        "regime_match",
        "mechanism_intact",
        "alternate_pathway",
    }
    assert DEFENSE_TYPES == expected


def test_format_critique_block_includes_all_fields():
    critique = Critique(
        target_id="n1",
        counterargument="weak link",
        score=0.6,
        attack_type="counter_example",
        cited_evidence=["FRED:CPIAUCSL"],
    )
    text = _format_critique_block(critique)
    assert "weak link" in text
    assert "0.60" in text
    assert "counter_example" in text
    assert "CPIAUCSL" in text


def test_format_critique_block_handles_missing_optionals():
    critique = Critique(target_id="n1", counterargument="vague", score=0.2)
    text = _format_critique_block(critique)
    assert "attack_type" not in text
    assert "cited_evidence" not in text


def test_parse_rebuttal_full_payload():
    text = """```json
{
  "target_id": "n1",
  "defense_type": "counter_evidence",
  "rebuttal": "Three episodes contradict the adversary's counter-example.",
  "cited_evidence": ["episode:2011 LTRO", "episode:2012 OMT"],
  "score": 0.75
}
```"""
    r = _parse_rebuttal(text, "fb")
    assert r.target_id == "n1"
    assert r.defense_type == "counter_evidence"
    assert r.score == 0.75
    assert "LTRO" in r.cited_evidence[0]


def test_parse_rebuttal_drops_unknown_defense_type():
    text = '```json\n{"target_id": "n1", "defense_type": "invented", "rebuttal": "x", "score": 0.5}\n```'
    assert _parse_rebuttal(text, "fb").defense_type is None


def test_parse_rebuttal_handles_unparseable():
    r = _parse_rebuttal("not json", "fallback")
    assert r.target_id == "fallback"
    assert r.score == 0.0
    assert "parser failed" in r.rebuttal.lower()


def test_run_passes_critique_into_user_message():
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(
        text='```json\n{"target_id": "n1", "defense_type": "regime_match", '
             '"rebuttal": "today matches", "score": 0.7}\n```'
    )]
    fake_client.messages.create.return_value = fake_msg

    critique = Critique(
        target_id="n1",
        counterargument="regime was different",
        score=0.6,
        attack_type="regime_mismatch",
    )
    result = defender.run(_node("n1", "X"), critique, client=fake_client)
    assert result.score == 0.7
    assert result.defense_type == "regime_match"

    user_msg = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "regime was different" in user_msg
    assert "regime_mismatch" in user_msg


def test_run_falls_back_to_critique_target_id():
    fake_client = MagicMock()
    fake_msg = MagicMock()
    # Response omits target_id; defender should fall back to critique.target_id.
    fake_msg.content = [MagicMock(text='```json\n{"rebuttal": "ok", "score": 0.5}\n```')]
    fake_client.messages.create.return_value = fake_msg

    edge = Edge(src="a", dst="b", mechanism="m", sensitivity=0.4, confidence=0.4,
                supporting_data=[Evidence(kind="fred_series", ref="X")])
    critique = Critique(target_id="a->b", counterargument="x", score=0.4)

    result = defender.run(edge, critique,
                          nodes={"a": _node("a", "A"), "b": _node("b", "B", layer=2)},
                          client=fake_client)
    assert result.target_id == "a->b"
