from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from src.types import MacroSnapshot, ToolBundle

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
