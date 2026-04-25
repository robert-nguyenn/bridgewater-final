from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from src.agents._common import (
    extract_json,
    format_target,
    target_id as _target_id,
)
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.config import ANTHROPIC_API_KEY, MODEL, PROMPTS_DIR
from src.types import Edge, Node, ToolBundle

VALID_DECISIONS = {"keep", "drop"}
VALID_EVIDENCE_WINNERS = {"adversary", "defender", "tie"}


@dataclass
class ModeratorVerdict:
    """Independent 4-pass judge of an adversary vs defender debate.

    Decomposed into structured fields so the audit trail explains *why* the
    decision was made (which side won evidence, whether defender addressed
    the attack directly), not just a scalar adjustment.
    """

    target_id: str
    decision: str  # "keep" | "drop"
    confidence_adjustment: float = 0.0
    reasoning: str = ""
    adversary_strongest_point: str = ""
    defender_strongest_response: str = ""
    defender_addresses_directly: bool = True
    evidence_winner: str = "tie"
    raw_response: Optional[str] = None


def _parse(text: str, fallback_id: str) -> ModeratorVerdict:
    parsed = extract_json(text)
    if parsed is None:
        return ModeratorVerdict(
            target_id=fallback_id,
            decision="keep",
            reasoning="(parser failed)",
            raw_response=text,
        )

    decision = str(parsed.get("decision", "keep")).lower()
    if decision not in VALID_DECISIONS:
        decision = "keep"

    raw_adj = parsed.get("confidence_adjustment", 0.0)
    try:
        adj = float(raw_adj)
    except (TypeError, ValueError):
        adj = 0.0
    adj = max(-0.3, min(0.2, adj))

    evidence_winner = str(parsed.get("evidence_winner", "tie")).lower()
    if evidence_winner not in VALID_EVIDENCE_WINNERS:
        evidence_winner = "tie"

    return ModeratorVerdict(
        target_id=str(parsed.get("target_id", fallback_id)),
        decision=decision,
        confidence_adjustment=adj,
        reasoning=str(parsed.get("reasoning", "")),
        adversary_strongest_point=str(parsed.get("adversary_strongest_point", "")),
        defender_strongest_response=str(parsed.get("defender_strongest_response", "")),
        defender_addresses_directly=bool(parsed.get("defender_addresses_directly", True)),
        evidence_winner=evidence_winner,
        raw_response=text,
    )


def _format_debate_block(critique: Critique, rebuttal: Rebuttal) -> str:
    lines = [
        "=== Adversary critique ===",
        f"attack_type: {critique.attack_type or '(none)'}",
        f"counterargument: {critique.counterargument}",
        f"cited_evidence: {critique.cited_evidence or '(none)'}",
        f"adversary score (case to remove): {critique.score:.2f}",
        "",
        "=== Defender rebuttal ===",
        f"defense_type: {rebuttal.defense_type or '(none)'}",
        f"rebuttal: {rebuttal.rebuttal}",
        f"cited_evidence: {rebuttal.cited_evidence or '(none)'}",
        f"defender score (case to keep): {rebuttal.score:.2f}",
    ]
    return "\n".join(lines)


def run(
    target: Union[Node, Edge],
    critique: Critique,
    rebuttal: Rebuttal,
    *,
    nodes: Optional[dict[str, Node]] = None,
    tools: Optional[ToolBundle] = None,
    model: str = MODEL,
    client: Any = None,
    run_id: Optional[str] = None,
) -> ModeratorVerdict:
    """Independent 4-pass judge over an adversary-defender debate.

    Runs the same Lean-style structured analysis pattern as LogicVerifier:
    decompose adversary, decompose defender, compare directly, decide.
    Returns a structured verdict with per-pass fields the demo can render.
    """
    fallback_id = _target_id(target)

    system_prompt = (PROMPTS_DIR / "moderator.md").read_text()
    target_text = format_target(target, nodes)
    debate_text = _format_debate_block(critique, rebuttal)
    user_text = f"{target_text}\n\n{debate_text}\n"

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
    return _parse(text, fallback_id)
