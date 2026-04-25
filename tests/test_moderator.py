from __future__ import annotations

from unittest.mock import MagicMock

from src.agents import moderator
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.agents.moderator import (
    VALID_DECISIONS,
    VALID_EVIDENCE_WINNERS,
    ModeratorVerdict,
    _format_debate_block,
    _parse,
)
from src.types import Edge, Node


def _node(nid: str, label: str = "X", layer: int = 1) -> Node:
    return Node(id=nid, label=label, description=f"{label} description", layer=layer)


def test_module_has_run():
    assert hasattr(moderator, "run")


def test_valid_decisions_are_keep_or_drop():
    assert VALID_DECISIONS == {"keep", "drop"}


def test_valid_evidence_winners():
    assert VALID_EVIDENCE_WINNERS == {"adversary", "defender", "tie"}


def test_parse_full_4pass_payload():
    text = """```json
{
  "target_id": "e1",
  "adversary_strongest_point": "2018 tariffs strengthened USD, contradicting the chain.",
  "defender_strongest_response": "1971 import surcharge weakened USD via offsetting capital flows.",
  "defender_addresses_directly": true,
  "evidence_winner": "defender",
  "decision": "keep",
  "confidence_adjustment": 0.05,
  "synthesis": "USD weakens via capital-flow channel, with confidence downgraded for tariff-specific exceptions.",
  "reasoning": "Defender directly countered with a dated episode; defender evidence wins."
}
```"""
    v = _parse(text, "fb")
    assert v.adversary_strongest_point.startswith("2018")
    assert v.defender_strongest_response.startswith("1971")
    assert v.defender_addresses_directly is True
    assert v.evidence_winner == "defender"
    assert v.decision == "keep"
    assert "capital-flow" in v.synthesis


def test_parse_synthesis_optional():
    """Old JSON without the synthesis field still parses; defaults to empty string."""
    text = '```json\n{"target_id": "e1", "decision": "keep"}\n```'
    v = _parse(text, "fb")
    assert v.synthesis == ""


def test_parse_unknown_evidence_winner_defaults_to_tie():
    text = '```json\n{"target_id": "e1", "decision": "keep", "evidence_winner": "neither"}\n```'
    assert _parse(text, "fb").evidence_winner == "tie"


def test_parse_missing_pass_fields_use_defaults():
    """Backward-compatible: old JSON shape (no per-pass fields) still parses."""
    text = '```json\n{"target_id": "e1", "decision": "drop", "confidence_adjustment": -0.1}\n```'
    v = _parse(text, "fb")
    assert v.decision == "drop"
    assert v.adversary_strongest_point == ""
    assert v.defender_addresses_directly is True
    assert v.evidence_winner == "tie"


def test_format_debate_block_includes_both_sides():
    crit = Critique(
        target_id="e1", counterargument="weak link", score=0.6,
        attack_type="counter_example", cited_evidence=["FRED:CPI"],
    )
    reb = Rebuttal(
        target_id="e1", rebuttal="solid", score=0.5,
        defense_type="counter_evidence", cited_evidence=["episode:LTRO"],
    )
    text = _format_debate_block(crit, reb)
    assert "weak link" in text
    assert "solid" in text
    assert "counter_example" in text
    assert "counter_evidence" in text
    assert "FRED:CPI" in text
    assert "LTRO" in text


def test_parse_full_keep_payload():
    text = '```json\n{"target_id": "e1", "decision": "keep", "confidence_adjustment": 0.1, "reasoning": "defender cited LTRO 2011"}\n```'
    v = _parse(text, "fallback")
    assert v.target_id == "e1"
    assert v.decision == "keep"
    assert abs(v.confidence_adjustment - 0.1) < 1e-9
    assert "LTRO" in v.reasoning


def test_parse_full_drop_payload():
    text = '```json\n{"target_id": "e1", "decision": "drop", "confidence_adjustment": -0.25, "reasoning": "adversary cited counter-example, defender abstract"}\n```'
    v = _parse(text, "fb")
    assert v.decision == "drop"
    assert v.confidence_adjustment == -0.25


def test_parse_unknown_decision_defaults_to_keep():
    text = '```json\n{"target_id": "e1", "decision": "abstain", "confidence_adjustment": 0.0}\n```'
    assert _parse(text, "fb").decision == "keep"


def test_parse_clamps_confidence_adjustment():
    high = '```json\n{"target_id": "e1", "decision": "keep", "confidence_adjustment": 0.9}\n```'
    low = '```json\n{"target_id": "e1", "decision": "drop", "confidence_adjustment": -0.9}\n```'
    assert _parse(high, "fb").confidence_adjustment == 0.2
    assert _parse(low, "fb").confidence_adjustment == -0.3


def test_parse_unparseable_defaults_to_keep_with_zero_adjustment():
    v = _parse("not json at all", "fallback_id")
    assert v.decision == "keep"
    assert v.confidence_adjustment == 0.0
    assert v.target_id == "fallback_id"
    assert "parser failed" in v.reasoning.lower()


def test_run_uses_injected_client_and_passes_both_transcripts():
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(
        text='```json\n{"target_id": "e1", "decision": "drop", "confidence_adjustment": -0.15, "reasoning": "adversary won"}\n```'
    )]
    fake_client.messages.create.return_value = fake_msg

    edge = Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.6, id="e1")
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    crit = Critique(target_id="e1", counterargument="weak", score=0.7, attack_type="counter_example")
    reb = Rebuttal(target_id="e1", rebuttal="vague", score=0.4)

    v = moderator.run(edge, crit, reb, nodes=nodes, client=fake_client)
    assert v.decision == "drop"
    assert v.confidence_adjustment == -0.15

    user_msg = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "weak" in user_msg
    assert "vague" in user_msg


def test_verdict_dataclass_default_factory():
    v = ModeratorVerdict(target_id="x", decision="keep")
    assert v.confidence_adjustment == 0.0
    assert v.reasoning == ""
