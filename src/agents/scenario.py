from __future__ import annotations

from dataclasses import dataclass

from src.types import ToolBundle


@dataclass
class TailScenario:
    text: str
    probability: float


def run(news_corpus: list[dict], *, tools: ToolBundle, model: str) -> list[TailScenario]:
    """Stretch. Generate tail policy scenarios with probability from a news corpus."""
    raise NotImplementedError
