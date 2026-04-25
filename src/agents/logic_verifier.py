from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.agents._common import extract_json
from src.config import ANTHROPIC_API_KEY, MODEL, PROMPTS_DIR
from src.types import Edge, Node, ToolBundle

FAILURE_CATEGORIES = {
    # Chain-validity failures
    "hidden_assumption",
    "mechanism_mismatch",
    "magnitude_leap",
    "equivocation",
    "time_mismatch",
    "missing_step",
    "sign_inconsistency",
    "score_evidence_mismatch",
    # Common LLM reasoning failures the verifier explicitly catches
    "reverse_causation",        # X→Y claimed when actual direction is Y→X
    "spurious_correlation",     # cites co-movement without a named mechanism
    "selection_bias",           # cites only confirming episodes, ignores misses
    "base_rate_neglect",        # extrapolates from one episode, ignores priors
    "fabricated_evidence",      # cites series/tickers/episodes that don't exist
    "levels_confusion",         # micro effect inflated to macro (or vice versa)
    "affirming_consequent",     # from "A→B" and "B observed", claims "A"
}


@dataclass
class StepAnalysis:
    edge_idx: int
    src_label: str
    dst_label: str
    mechanism: str
    preconditions: list[str] = field(default_factory=list)
    sign: str = "unclear"
    magnitude_class: str = "unclear"
    horizon: str = "unclear"
    local_ok: bool = True
    local_reason: str = ""


@dataclass
class ConsistencyIssue:
    edge_idx: int
    field: str  # "confidence" | "sensitivity"
    score: float
    evidence_count: int
    expected: str
    severity: str  # "fail" | "warning"


@dataclass
class VerificationResult:
    ok: bool
    reason: str
    failed_edge_idx: Optional[int] = None
    failure_category: Optional[str] = None
    step_analyses: list[StepAnalysis] = field(default_factory=list)
    consistency_issues: list[ConsistencyIssue] = field(default_factory=list)
    raw_response: Optional[str] = None


def check_score_evidence_consistency(chain: list[Edge]) -> list[ConsistencyIssue]:
    """Structural check: each edge's scores must be backed by enough cited evidence.

    Hard rule (CLAUDE.md): any score above 0.3 requires a citation. Soft rules
    (rubric): 0.6+ expects 2+ episodes, 0.85+ expects 3+, sensitivity 0.8+
    expects 2+. Hard rule produces severity 'fail', rubric produces 'warning'.
    """
    issues: list[ConsistencyIssue] = []
    for i, edge in enumerate(chain):
        n = len(edge.supporting_data)

        # Hard rule: score above 0.3 needs a citation.
        if edge.confidence > 0.3 and n == 0:
            issues.append(ConsistencyIssue(
                edge_idx=i,
                field="confidence",
                score=edge.confidence,
                evidence_count=n,
                expected="confidence > 0.3 requires at least 1 cited source",
                severity="fail",
            ))
        if edge.sensitivity > 0.3 and n == 0:
            issues.append(ConsistencyIssue(
                edge_idx=i,
                field="sensitivity",
                score=edge.sensitivity,
                evidence_count=n,
                expected="sensitivity > 0.3 requires at least 1 cited source",
                severity="fail",
            ))

        # Rubric warnings.
        if edge.confidence > 0.6 and n < 2:
            issues.append(ConsistencyIssue(
                edge_idx=i,
                field="confidence",
                score=edge.confidence,
                evidence_count=n,
                expected="confidence > 0.6 expects 2+ cited episodes per rubric",
                severity="warning",
            ))
        if edge.confidence > 0.85 and n < 3:
            issues.append(ConsistencyIssue(
                edge_idx=i,
                field="confidence",
                score=edge.confidence,
                evidence_count=n,
                expected="confidence > 0.85 expects 3+ cited episodes per rubric",
                severity="warning",
            ))
        if edge.sensitivity > 0.8 and n < 2:
            issues.append(ConsistencyIssue(
                edge_idx=i,
                field="sensitivity",
                score=edge.sensitivity,
                evidence_count=n,
                expected="sensitivity > 0.8 expects multiple episodes per rubric",
                severity="warning",
            ))
    return issues


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
    parsed = extract_json(text)
    if parsed is None:
        return VerificationResult(
            ok=False,
            reason="could not parse verifier response as JSON",
            raw_response=text,
        )

    steps = [_coerce_step(s) for s in parsed.get("step_analyses", [])]

    category = parsed.get("failure_category")
    if category is not None and category not in FAILURE_CATEGORIES:
        category = None

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


def _merge_consistency(result: VerificationResult, issues: list[ConsistencyIssue]) -> VerificationResult:
    """Attach issues; if any are 'fail' and the LLM verdict was ok, override to fail."""
    result.consistency_issues = issues
    fails = [c for c in issues if c.severity == "fail"]
    if fails and result.ok:
        first = fails[0]
        result.ok = False
        result.failure_category = "score_evidence_mismatch"
        result.failed_edge_idx = first.edge_idx
        result.reason = (
            f"score-evidence mismatch on edge {first.edge_idx}: "
            f"{first.expected} (got {first.evidence_count})"
        )
    return result


def run(
    chain: list[Edge],
    *,
    nodes: dict[str, Node],
    tools: Optional[ToolBundle] = None,
    model: str = MODEL,
    client: Any = None,
) -> VerificationResult:
    """Lean-style local validity check on a causal chain plus a structural
    score-vs-evidence consistency check.

    `nodes` maps node id to Node so the verifier sees labels and descriptions
    rather than just edge endpoint ids. `client` is an injected anthropic
    client for testability; if None, one is constructed from env.
    """
    if not chain:
        return VerificationResult(ok=True, reason="empty chain")

    issues = check_score_evidence_consistency(chain)

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
    result = _parse_response(text)
    return _merge_consistency(result, issues)
