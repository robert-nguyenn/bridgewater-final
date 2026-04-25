from __future__ import annotations

from dataclasses import dataclass

from src.types import MacroSnapshot, ToolBundle


@dataclass
class ComparatorResult:
    similarity: float
    diverging_dimensions: list[str]


def run(
    then: MacroSnapshot,
    now: MacroSnapshot,
    *,
    tools: ToolBundle,
    model: str,
) -> ComparatorResult:
    """Compare two MacroSnapshots and return a similarity score plus diverging dims."""
    raise NotImplementedError
