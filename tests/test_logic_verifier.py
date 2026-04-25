from __future__ import annotations

from unittest.mock import MagicMock

from src.agents import logic_verifier
from src.agents.logic_verifier import (
    FAILURE_CATEGORIES,
    _extract_json,
    _format_chain,
    _parse_response,
)
from src.types import Edge, Node


def _node(nid: str, label: str, layer: int = 1) -> Node:
    return Node(id=nid, label=label, description=f"{label} description", layer=layer)


def test_logic_verifier_module_has_run():
    assert hasattr(logic_verifier, "run")


def test_empty_chain_passes_without_api_call():
    result = logic_verifier.run([], nodes={})
    assert result.ok is True
    assert "empty" in result.reason.lower()


def test_format_chain_includes_labels_descriptions_and_mechanism():
    nodes = {
        "a": _node("a", "USD strengthens"),
        "b": _node("b", "Chip ASP rises", layer=2),
    }
    chain = [
        Edge(src="a", dst="b", mechanism="cost pass-through", sensitivity=0.6, confidence=0.7),
    ]
    text = _format_chain(chain, nodes)
    assert "USD strengthens" in text
    assert "Chip ASP rises" in text
    assert "cost pass-through" in text
    assert "0.60" in text
    assert "0.70" in text


def test_extract_json_from_fenced_block():
    text = """thinking out loud here
```json
{"ok": true, "reason": "fine"}
```
done."""
    assert _extract_json(text) == {"ok": True, "reason": "fine"}


def test_extract_json_falls_back_to_naked_braces():
    text = 'preamble {"ok": false, "reason": "bad"} trailing'
    assert _extract_json(text) == {"ok": False, "reason": "bad"}


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("totally not json at all") is None


def test_parse_response_handles_full_failure_payload():
    text = """```json
{
  "ok": false,
  "reason": "step 1 has a magnitude leap",
  "failed_edge_idx": 1,
  "failure_category": "magnitude_leap",
  "step_analyses": [
    {"edge_idx": 0, "src_label": "A", "dst_label": "B", "mechanism": "m1",
     "preconditions": [], "sign": "+", "magnitude_class": "small", "horizon": "short",
     "local_ok": true, "local_reason": ""},
    {"edge_idx": 1, "src_label": "B", "dst_label": "C", "mechanism": "m2",
     "preconditions": ["no Fed reaction"], "sign": "+", "magnitude_class": "large",
     "horizon": "long", "local_ok": false, "local_reason": "small to large with no amplifier"}
  ]
}
```"""
    result = _parse_response(text)
    assert result.ok is False
    assert result.failed_edge_idx == 1
    assert result.failure_category == "magnitude_leap"
    assert len(result.step_analyses) == 2
    assert result.step_analyses[1].local_ok is False
    assert result.step_analyses[1].preconditions == ["no Fed reaction"]


def test_parse_response_drops_unknown_failure_category():
    text = '```json\n{"ok": false, "reason": "x", "failed_edge_idx": 0, "failure_category": "made_up"}\n```'
    result = _parse_response(text)
    assert result.failure_category is None


def test_parse_response_handles_unparseable_text():
    result = _parse_response("totally not json")
    assert result.ok is False
    assert "parse" in result.reason.lower()
    assert result.raw_response == "totally not json"


def test_failure_categories_match_prompt_enum():
    expected = {
        "hidden_assumption",
        "mechanism_mismatch",
        "magnitude_leap",
        "equivocation",
        "time_mismatch",
        "missing_step",
        "sign_inconsistency",
    }
    assert FAILURE_CATEGORIES == expected


def test_run_uses_injected_client():
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [
        MagicMock(text='```json\n{"ok": true, "reason": "looks fine", "step_analyses": []}\n```')
    ]
    fake_client.messages.create.return_value = fake_msg

    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    chain = [Edge(src="a", dst="b", mechanism="m", sensitivity=0.5, confidence=0.5)]

    result = logic_verifier.run(chain, nodes=nodes, client=fake_client)
    assert result.ok is True
    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert "system" in call_kwargs
    assert "Lean" in call_kwargs["system"] or "lean" in call_kwargs["system"].lower()
