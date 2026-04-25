from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Optional

from src.agents import adversary, defender
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.config import MODEL
from src.types import CausalGraph, ToolBundle


@dataclass
class Debate:
    target_id: str
    critique: Critique
    rebuttal: Rebuttal

    @property
    def survives(self) -> bool:
        """Defender wins ties. Pruner can apply a stricter margin if needed."""
        return self.rebuttal.score >= self.critique.score

    @property
    def margin(self) -> float:
        """Positive means defender ahead, negative means adversary ahead."""
        return self.rebuttal.score - self.critique.score


def run_adversarial_debate(
    graph: CausalGraph,
    *,
    include_nodes: bool = False,
    model: str = MODEL,
    client: Any = None,
) -> dict[str, Debate]:
    """Stage 5. Run AdversaryAgent then DefenderAgent on every edge in the graph.

    Returns a dict keyed by target_id (edge.id, or node.id when include_nodes=True).
    Pruner consumes this dict alongside MacroComparator scores in stage 7.
    """
    debates: dict[str, Debate] = {}

    for edge in graph.edges:
        critique = adversary.run(edge, nodes=graph.nodes, model=model, client=client)
        rebuttal = defender.run(
            edge, critique, nodes=graph.nodes, model=model, client=client
        )
        debates[edge.id] = Debate(
            target_id=edge.id, critique=critique, rebuttal=rebuttal
        )

    if include_nodes:
        for node in graph.nodes.values():
            critique = adversary.run(node, nodes=graph.nodes, model=model, client=client)
            rebuttal = defender.run(
                node, critique, nodes=graph.nodes, model=model, client=client
            )
            debates[node.id] = Debate(
                target_id=node.id, critique=critique, rebuttal=rebuttal
            )

    return debates


def run_pipeline(event: str, *, dry_run: bool = False, model: str = MODEL) -> CausalGraph:
    """Stage 1 to 10. See CLAUDE.md 'Pipeline (end to end)'."""
    tools = ToolBundle()  # noqa: F841 — wired into stages once they're real
    graph = CausalGraph()

    # TODO(integration): stage 1 IdeaAgent -> first-order Nodes
    # TODO(integration): stage 2 AnalogSearchAgent -> Episodes per first-order Node
    # TODO(integration): stage 3 TreeBuilder + Sensitivity -> per-analog subtrees
    # TODO(integration): stage 4 LogicVerifier -> drop chains that fail

    # Stage 5: Adversarial debate. No-op until upstream stages produce edges.
    if not dry_run and graph.edges:
        run_adversarial_debate(graph, model=model)
        # TODO(integration): pass returned debates dict to stage 7 PrunerAgent

    # TODO(integration): stage 6 MacroComparator
    # TODO(integration): stage 7 PrunerAgent uses debates + comparator
    # TODO(integration): stage 8 second TreeBuilder pass
    # TODO(integration): stage 9 PortfolioAgent
    # TODO(integration): stage 10 ScenarioAgent (stretch)

    if dry_run:
        print(f"[dry-run] event={event!r} model={model}")
    return graph


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="policy-mapper")
    parser.add_argument("--event", required=True, help="plain English policy event")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    run_pipeline(args.event, dry_run=args.dry_run, model=args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
