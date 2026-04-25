from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

from src.agents._common import (
    clamp_score,
    extract_json,
    format_target,
    target_id as _target_id,
)
from src.config import ANTHROPIC_API_KEY, MODEL, PROMPTS_DIR
from src.types import Edge, Node, ToolBundle

ATTACK_TYPES = {
    "counter_example",
    "structural_objection",
    "magnitude_doubt",
    "transmission_break",
    "regime_mismatch",
    "precondition_failure",
}


@dataclass
class Critique:
    target_id: str
    counterargument: str
    score: float  # [0, 1] — higher means stronger case to remove
    attack_type: Optional[str] = None
    cited_evidence: list[str] = field(default_factory=list)
    raw_response: Optional[str] = None


def _parse_critique(text: str, fallback_id: str) -> Critique:
    parsed = extract_json(text)
    if parsed is None:
        return Critique(
            target_id=fallback_id,
            counterargument="(parser failed)",
            score=0.0,
            raw_response=text,
        )

    attack_type = parsed.get("attack_type")
    if attack_type is not None and attack_type not in ATTACK_TYPES:
        attack_type = None

    return Critique(
        target_id=str(parsed.get("target_id", fallback_id)),
        counterargument=str(parsed.get("counterargument", "")),
        score=clamp_score(parsed.get("score", 0.0)),
        attack_type=attack_type,
        cited_evidence=[str(c) for c in parsed.get("cited_evidence", [])],
        raw_response=text,
    )


def run(
    target: Union[Node, Edge],
    *,
    nodes: Optional[dict[str, Node]] = None,
    tools: Optional[ToolBundle] = None,
    model: str = MODEL,
    client: Any = None,
) -> Critique:
    """Argue against a Node or Edge. Return Critique with score in [0, 1]."""
    fallback_id = _target_id(target)

    system_prompt = (PROMPTS_DIR / "adversary.md").read_text()
    user_text = format_target(target, nodes)

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )

    text = msg.content[0].text if msg.content else ""
    return _parse_critique(text, fallback_id)
