from __future__ import annotations

from unittest.mock import MagicMock

from src.agents import logic_verifier
from src.agents._common import extract_json
from src.agents.logic_verifier import (
    FAILURE_CATEGORIES,
    _format_chain,
    _parse_response,
    check_score_evidence_consistency,
)
from src.types import Edge, Evidence, Node


def _node(nid: str, label: str, layer: int = 1) -> Node:
    return Node(id=nid, label=label, description=f"{label} description", layer=layer)


def _edge(src: str, dst: str, sens: float, conf: float, n_evidence: int = 0) -> Edge:
    ev = [Evidence(kind="fred_series", ref=f"FRED{i}") for i in range(n_evidence)]
    return Edge(src=src, dst=dst, mechanism="m", sensitivity=sens, confidence=conf, supporting_data=ev)


def test_logic_verifier_module_has_run():
    assert hasattr(logic_verifier, "run")


def test_empty_chain_passes_without_api_call():
    result = logic_verifier.run([], nodes={})
    assert result.ok is True
    assert "empty" in result.reason.lower()


def test_format_chain_includes_labels_descriptions_and_mechanism():
    nodes = {"a": _node("a", "USD strengthens"), "b": _node("b", "Chip ASP rises", layer=2)}
    chain = [Edge(src="a", dst="b", mechanism="cost pass-through", sensitivity=0.6, confidence=0.7)]
    text = _format_chain(chain, nodes)
    assert "USD strengthens" in text
    assert "Chip ASP rises" in text
    assert "cost pass-through" in text
    assert "0.60" in text and "0.70" in text


def test_extract_json_from_fenced_block():
    text = '```json\n{"ok": true, "reason": "fine"}\n```'
    assert extract_json(text) == {"ok": True, "reason": "fine"}


def test_extract_json_falls_back_to_naked_braces():
    assert extract_json('preamble {"ok": false} trailing') == {"ok": False}


def test_extract_json_returns_none_on_garbage():
    assert extract_json("not json") is None


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


def test_parse_response_drops_unknown_failure_category():
    text = '```json\n{"ok": false, "failure_category": "made_up", "failed_edge_idx": 0}\n```'
    assert _parse_response(text).failure_category is None


def test_parse_response_handles_unparseable_text():
    result = _parse_response("totally not json")
    assert result.ok is False
    assert "parse" in result.reason.lower()
    assert result.raw_response == "totally not json"


def test_failure_categories_includes_score_evidence_mismatch():
    assert "score_evidence_mismatch" in FAILURE_CATEGORIES


# Consistency check tests

def test_consistency_no_issues_for_low_scores():
    chain = [_edge("a", "b", sens=0.2, conf=0.2)]
    assert check_score_evidence_consistency(chain) == []


def test_consistency_fails_high_confidence_no_evidence():
    chain = [_edge("a", "b", sens=0.0, conf=0.8, n_evidence=0)]
    issues = check_score_evidence_consistency(chain)
    fails = [i for i in issues if i.severity == "fail"]
    assert any(i.field == "confidence" for i in fails)


def test_consistency_fails_high_sensitivity_no_evidence():
    chain = [_edge("a", "b", sens=0.6, conf=0.0, n_evidence=0)]
    issues = check_score_evidence_consistency(chain)
    fails = [i for i in issues if i.severity == "fail"]
    assert any(i.field == "sensitivity" for i in fails)


def test_consistency_warns_high_confidence_one_episode():
    chain = [_edge("a", "b", sens=0.0, conf=0.7, n_evidence=1)]
    issues = check_score_evidence_consistency(chain)
    warnings = [i for i in issues if i.severity == "warning"]
    assert any("0.6" in i.expected for i in warnings)


def test_consistency_warns_very_high_confidence_two_episodes():
    chain = [_edge("a", "b", sens=0.0, conf=0.9, n_evidence=2)]
    issues = check_score_evidence_consistency(chain)
    warnings = [i for i in issues if i.severity == "warning"]
    assert any("0.85" in i.expected for i in warnings)


def test_consistency_clean_with_enough_episodes():
    chain = [_edge("a", "b", sens=0.5, conf=0.5, n_evidence=2)]
    assert check_score_evidence_consistency(chain) == []


# Run-level integration

def _fake_client_returning(payload_text: str) -> MagicMock:
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(text=payload_text)]
    fake_client.messages.create.return_value = fake_msg
    return fake_client


def test_run_consistency_fail_overrides_llm_pass():
    """If LLM said ok but consistency check finds a hard fail, result flips to fail."""
    fake_client = _fake_client_returning(
        '```json\n{"ok": true, "reason": "looks fine", "step_analyses": []}\n```'
    )
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    chain = [_edge("a", "b", sens=0.0, conf=0.7, n_evidence=0)]  # hard fail

    result = logic_verifier.run(chain, nodes=nodes, client=fake_client)
    assert result.ok is False
    assert result.failure_category == "score_evidence_mismatch"
    assert result.failed_edge_idx == 0
    assert any(i.severity == "fail" for i in result.consistency_issues)


def test_run_warnings_only_does_not_override_llm_pass():
    """Warning-only consistency issues are surfaced but do not flip ok."""
    fake_client = _fake_client_returning(
        '```json\n{"ok": true, "reason": "looks fine", "step_analyses": []}\n```'
    )
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    chain = [_edge("a", "b", sens=0.0, conf=0.7, n_evidence=1)]  # warning only

    result = logic_verifier.run(chain, nodes=nodes, client=fake_client)
    assert result.ok is True
    assert any(i.severity == "warning" for i in result.consistency_issues)


def test_run_uses_injected_client_and_passes_system_prompt():
    fake_client = _fake_client_returning(
        '```json\n{"ok": true, "reason": "fine", "step_analyses": []}\n```'
    )
    nodes = {"a": _node("a", "A"), "b": _node("b", "B", layer=2)}
    chain = [_edge("a", "b", sens=0.2, conf=0.2)]

    logic_verifier.run(chain, nodes=nodes, client=fake_client)
    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert "system" in call_kwargs
    assert "lean" in call_kwargs["system"].lower()
