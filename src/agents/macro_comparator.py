from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from src.agents._common import extract_json
from src.config import ANTHROPIC_API_KEY, MODEL, MODEL_FAST, PROMPTS_DIR
from src.types import MacroSnapshot, ToolBundle

logger = logging.getLogger(__name__)

# Per-field "meaningful difference" scales. Distance is measured in units of
# this scale so a 200bp Fed funds gap is comparable to a 10pt DXY gap. Tune
# these by checking how far typical regimes drift on each dimension.
FIELD_SCALES: dict[str, float] = {
    "cpi_yoy": 2.0,
    "core_pce_yoy": 1.5,
    "fed_funds": 2.0,
    "ten_year": 1.5,
    "dxy": 10.0,
    "unemployment": 2.0,
    "real_gdp_yoy": 2.0,
}


@dataclass
class ComparatorResult:
    similarity: float  # in [0, 1]; 1.0 = identical, ~0.37 = ~1 scale apart on every field
    diverging_dimensions: list[str] = field(default_factory=list)
    distances: dict[str, float] = field(default_factory=dict)


@dataclass
class LinkApplicability:
    """Result of the per-bridge applies-today check.

    `applies` is a hard boolean (mean distance ≤ threshold). `confidence` is a
    soft signal in [0, 1] used to multiply the bridge's confidence so links
    in close-but-not-identical regimes still attach but are downweighted.
    """

    applies: bool
    confidence: float
    relevant_indices: list[str] = field(default_factory=list)
    distances: dict[str, float] = field(default_factory=dict)
    reasoning: str = ""


_INDEX_PICKER_SYSTEM = """You are a macro analyst. Given a single causal linkage
(parent variable → child variable via a named mechanism), identify which macro
indices most determine whether this linkage applies in today's regime versus
the case study's regime. The downstream pipeline will measure the distance
between then and now on those indices and decide whether to attach the link.

Available indices and what they govern:
- cpi_yoy: headline CPI YoY (inflation regime).
- core_pce_yoy: core PCE YoY (underlying inflation regime).
- fed_funds: Fed funds effective rate (monetary policy stance).
- ten_year: 10y UST yield (long-end financial conditions).
- dxy: USD broad index (dollar regime).
- unemployment: US unemployment rate (labor market slack).
- real_gdp_yoy: real GDP YoY (growth regime).

Pick 1 to 4 indices that most govern whether THIS specific linkage's mechanism
fires today. Be selective — picking everything dilutes the signal. Reasoning
should name the specific transmission channel."""


def _pick_relevant_indices(
    parent_label: str,
    child_label: str,
    mechanism: str,
    *,
    model: str,
    client: Any,
) -> tuple[list[str], str]:
    """Step 1: ask the model which macro indices govern this linkage's
    applicability. Returns (indices, reasoning). Empty list on any failure."""
    user = (
        f"Linkage: {parent_label} → {child_label}\n"
        f"Mechanism: {mechanism}\n\n"
        "Which macro indices most determine whether this linkage applies "
        "in today's regime? Return JSON only:\n"
        '{"indices": ["fed_funds", "ten_year"], "reasoning": "one sentence"}'
    )
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=512,
            system=_INDEX_PICKER_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning("link_applicability index picker failed: %s", exc)
        return [], ""
    text = msg.content[0].text if msg.content else ""
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        return [], ""
    raw = parsed.get("indices", [])
    if not isinstance(raw, list):
        return [], ""
    valid = [s for s in raw if isinstance(s, str) and s in FIELD_SCALES]
    return valid[:4], str(parsed.get("reasoning") or "")


def link_applicability(
    parent_label: str,
    child_label: str,
    mechanism: str,
    then_snapshot: MacroSnapshot,
    now_snapshot: MacroSnapshot,
    *,
    model: str = MODEL_FAST,
    client: Any = None,
    distance_threshold: float = 1.5,
    fallback_indices: tuple[str, ...] = ("fed_funds", "cpi_yoy", "ten_year"),
) -> LinkApplicability:
    """Per-link applies-today check used to filter case-study bridge edges
    in stage 7 of the pipeline.

    Two steps:
    1. **Pick** (LLM): which macro indices govern this specific linkage?
    2. **Compare** (structural): on those indices only, compute normalized
       distance (using FIELD_SCALES). Average is the per-link regime gap.

    `applies = mean_distance <= distance_threshold`. `confidence = exp(-mean_distance)`
    in [0, 1]; identical regimes give 1.0, ~1 scale gap gives ~0.37. The
    orchestrator multiplies the case study's overall similarity_score by this
    confidence to set the bridge edge's sensitivity/confidence — so a strong
    case-study match plus a strong per-link match produces a confident bridge,
    and either side weakening pulls the bridge down accordingly.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    relevant, picker_reasoning = _pick_relevant_indices(
        parent_label, child_label, mechanism, model=model, client=client
    )
    if not relevant:
        relevant = list(fallback_indices)
        picker_reasoning = picker_reasoning or "(fell back to default rate/inflation/long-end indices)"

    distances: dict[str, float] = {}
    for fname in relevant:
        scale = FIELD_SCALES.get(fname)
        if scale is None:
            continue
        a = getattr(then_snapshot, fname, None)
        b = getattr(now_snapshot, fname, None)
        if a is None or b is None:
            continue
        try:
            distances[fname] = abs(float(a) - float(b)) / scale
        except (TypeError, ValueError):
            continue

    if not distances:
        return LinkApplicability(
            applies=True,
            confidence=0.5,
            relevant_indices=relevant,
            distances={},
            reasoning=(
                f"No comparable macro data for {relevant}; default-applies at "
                "neutral confidence. Picker: " + picker_reasoning
            ),
        )

    mean_d = sum(distances.values()) / len(distances)
    confidence = math.exp(-mean_d)
    applies = mean_d <= distance_threshold

    return LinkApplicability(
        applies=applies,
        confidence=round(confidence, 4),
        relevant_indices=relevant,
        distances={k: round(v, 4) for k, v in distances.items()},
        reasoning=(
            f"avg normalized distance on {relevant} = {mean_d:.2f} "
            f"({'within' if applies else 'beyond'} threshold {distance_threshold:.1f}). "
            f"Picker: {picker_reasoning}"
        ),
    )


def run(
    then: MacroSnapshot,
    now: MacroSnapshot,
    *,
    tools: Optional[ToolBundle] = None,
    model: str = "",
    client: Any = None,
    run_id: Optional[str] = None,
) -> ComparatorResult:
    """Structurally compare two MacroSnapshots and return a regime similarity score.

    Per-field absolute difference is normalized by FIELD_SCALES, then averaged.
    similarity = exp(-mean_distance), so identical snapshots score 1.0 and
    snapshots that differ by ~1 scale on every field score ~0.37.

    Fields where either snapshot is None are skipped (not penalized as zero).
    `diverging_dimensions` lists the top-3 fields ranked by normalized distance.

    `tools`, `model`, `client`, `run_id` are kept on the signature for API
    consistency with the canonical agent shape but are unused: this comparator
    is fully deterministic and avoids an LLM call when the input is numeric."""
    distances: dict[str, float] = {}
    for fname, scale in FIELD_SCALES.items():
        a = getattr(then, fname, None)
        b = getattr(now, fname, None)
        if a is None or b is None:
            continue
        try:
            distances[fname] = abs(float(a) - float(b)) / scale
        except (TypeError, ValueError):
            continue

    if not distances:
        return ComparatorResult(similarity=0.0)

    mean_d = sum(distances.values()) / len(distances)
    similarity = math.exp(-mean_d)
    # Only call out fields that actually diverged. Identical snapshots have nothing to flag.
    diverging = sorted(
        (k for k, v in distances.items() if v > 0),
        key=lambda k: distances[k],
        reverse=True,
    )[:3]

    return ComparatorResult(
        similarity=round(similarity, 4),
        diverging_dimensions=diverging,
        distances={k: round(v, 4) for k, v in distances.items()},
    )
