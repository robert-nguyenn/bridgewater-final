from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config import ANTHROPIC_API_KEY, MODEL, PROMPTS_DIR
from src.types import Edge, Node, ToolBundle

FAILURE_CATEGORIES = {
    "hidden_assumption",
    "mechanism_mismatch",
    "magnitude_leap",
    "equivocation",
    "time_mismatch",
    "missing_step",
    "sign_inconsistency",
}


@dataclass
class StepAnalysis:
    edge_idx: int
    src_label: str
    dst_label: str
    mechanism: str
    preconditions: list[str] = field(default_factory=list)
    sign: str = "unclear"  # "+", "-", "0", "unclear"
    magnitude_class: str = "unclear"  # "small", "medium", "large", "unclear"
    horizon: str = "unclear"  # "short", "medium", "long", "unclear"
    local_ok: bool = True
    local_reason: str = ""


@dataclass
class VerificationResult:
    ok: bool
    reason: str
    failed_edge_idx: Optional[int] = None
    failure_category: Optional[str] = None
    step_analyses: list[StepAnalysis] = field(default_factory=list)
    raw_response: Optional[str] = None


def _format_chain(chain: list[Edge], nodes: dict[str, Node]) -> str:
    lines: list[str] = ["Causal chain to verify, ordered left to right.", ""]
    for i, e in enumerate(chain):
        src = nodes.get(e.src)
        dst = nodes.get(e.dst)
        src_label = src.label if src else e.src
        dst_label = dst.label if dst else e.dst
        lines.append(f"Step {i}: [{e.src}] {src_label}  ->  [{e.dst}] {dst_label}")
        if src and src.description:
            lines.append(f"  source description: {src.description}")
        if dst and dst.description:
            lines.append(f"  destination description: {dst.description}")
        lines.append(f"  named mechanism: {e.mechanism}")
        lines.append(
            f"  agent sensitivity: {e.sensitivity:.2f}   "
            f"agent confidence: {e.confidence:.2f}"
        )
        lines.append("")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Find the JSON payload. Prefer the last fenced ```json block, else first {...}."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced[-1])
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _coerce_step(raw: dict[str, Any]) -> StepAnalysis:
    return StepAnalysis(
        edge_idx=int(raw.get("edge_idx", -1)),
        src_label=str(raw.get("src_label", "")),
        dst_label=str(raw.get("dst_label", "")),
        mechanism=str(raw.get("mechanism", "")),
        preconditions=[str(p) for p in raw.get("preconditions", [])],
        sign=str(raw.get("sign", "unclear")),
        magnitude_class=str(raw.get("magnitude_class", "unclear")),
        horizon=str(raw.get("horizon", "unclear")),
        local_ok=bool(raw.get("local_ok", True)),
        local_reason=str(raw.get("local_reason", "")),
    )


def _parse_response(text: str) -> VerificationResult:
    parsed = _extract_json(text)
    if parsed is None:
        return VerificationResult(
            ok=False,
            reason="could not parse verifier response as JSON",
            raw_response=text,
        )

    steps = [_coerce_step(s) for s in parsed.get("step_analyses", [])]

    category = parsed.get("failure_category")
    if category is not None and category not in FAILURE_CATEGORIES:
        category = None  # drop unknown enum values, keep ok flag honest

    failed_idx = parsed.get("failed_edge_idx")
    if failed_idx is not None:
        try:
            failed_idx = int(failed_idx)
        except (TypeError, ValueError):
            failed_idx = None

    return VerificationResult(
        ok=bool(parsed.get("ok", False)),
        reason=str(parsed.get("reason", "")),
        failed_edge_idx=failed_idx,
        failure_category=category,
        step_analyses=steps,
        raw_response=text,
    )


def run(
    chain: list[Edge],
    *,
    nodes: dict[str, Node],
    tools: Optional[ToolBundle] = None,
    model: str = MODEL,
    client: Any = None,
) -> VerificationResult:
    """Lean-style local validity check on a causal chain.

    `nodes` maps node id to Node so the verifier can see labels and descriptions
    rather than just edge endpoint ids. `client` is an injected anthropic client
    for testability; if None, a fresh one is constructed from env.
    """
    if not chain:
        return VerificationResult(ok=True, reason="empty chain")

    system_prompt = (PROMPTS_DIR / "logic_verifier.md").read_text()
    user_text = _format_chain(chain, nodes)

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )

    text = msg.content[0].text if msg.content else ""
    return _parse_response(text)
