from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from src.agents.adversary import Critique
from src.types import Edge, Node, ToolBundle


@dataclass
class Rebuttal:
    target_id: str
    rebuttal: str
    score: float


def run(
    target: Union[Node, Edge],
    critique: Critique,
    *,
    tools: ToolBundle,
    model: str,
) -> Rebuttal:
    """Defend a Node or Edge against an adversary Critique."""
    raise NotImplementedError
