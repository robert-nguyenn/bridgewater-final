from __future__ import annotations

from dataclasses import dataclass

from src.types import Edge, ToolBundle


@dataclass
class VerificationResult:
    ok: bool
    reason: str


def run(chain: list[Edge], *, tools: ToolBundle, model: str) -> VerificationResult:
    """Lean style local validity check on a causal chain."""
    raise NotImplementedError
