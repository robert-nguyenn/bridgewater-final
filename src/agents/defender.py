from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

from src.agents._common import (
    clamp_score,
    extract_json,
    format_target,
)
from src.agents.adversary import Critique
from src.config import ANTHROPIC_API_KEY, MODEL, PROMPTS_DIR
from src.types import Edge, Node, ToolBundle

DEFENSE_TYPES = {
    "counter_evidence",
    "precondition_holds",
    "magnitude_robust",
    "regime_match",
    "mechanism_intact",
    "alternate_pathway",
}


@dataclass
class Rebuttal:
    target_id: str
    rebuttal: str
    score: float  # [0, 1] — higher means stronger case to keep
    defense_type: Optional[str] = None
    cited_evidence: list[str] = field(default_factory=list)
    raw_response: Optional[str] = None


def _parse_rebuttal(text: str, fallback_id: str) -> Rebuttal:
    parsed = extract_json(text)
    if parsed is None:
        return Rebuttal(
            target_id=fallback_id,
            rebuttal="(parser failed)",
            score=0.0,
            raw_response=text,
        )

    defense_type = parsed.get("defense_type")
    if defense_type is not None and defense_type not in DEFENSE_TYPES:
        defense_type = None

    return Rebuttal(
        target_id=str(parsed.get("target_id", fallback_id)),
        rebuttal=str(parsed.get("rebuttal", "")),
        score=clamp_score(parsed.get("score", 0.0)),
        defense_type=defense_type,
        cited_evidence=[str(c) for c in parsed.get("cited_evidence", [])],
        raw_response=text,
    )


def _format_critique_block(critique: Critique) -> str:
    lines = [
        "=== Adversary critique ===",
        f"counterargument: {critique.counterargument}",
        f"adversary score (case to remove): {critique.score:.2f}",
    ]
    if critique.attack_type:
        lines.append(f"attack_type: {critique.attack_type}")
    if critique.cited_evidence:
        lines.append(f"cited_evidence: {critique.cited_evidence}")
    return "\n".join(lines)


def run(
    target: Union[Node, Edge],
    critique: Critique,
    *,
    nodes: Optional[dict[str, Node]] = None,
    tools: Optional[ToolBundle] = None,
    model: str = MODEL,
    client: Any = None,
) -> Rebuttal:
    """Defend a Node or Edge against an adversary Critique."""
    fallback_id = critique.target_id

    system_prompt = (PROMPTS_DIR / "defender.md").read_text()
    target_text = format_target(target, nodes)
    user_text = f"{target_text}\n\n{_format_critique_block(critique)}\n"

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
    return _parse_rebuttal(text, fallback_id)
