from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from src.types import Edge, Node, ToolBundle


@dataclass
class Critique:
    target_id: str
    counterargument: str
    score: float


def run(target: Union[Node, Edge], *, tools: ToolBundle, model: str) -> Critique:
    """Argue against a Node or Edge. Return counterargument plus score in [0, 1]."""
    raise NotImplementedError
