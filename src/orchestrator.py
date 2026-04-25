from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from src.agents import (
    adversary,
    analog_search,
    defender,
    idea,
    macro_comparator,
    portfolio,
    pruner,
    tree_builder,
)
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.agents.macro_comparator import ComparatorResult
from src.agents.portfolio import PortfolioImpact
from src.config import MODEL
from src.tools import make_default_tools
from src.types import (
    CaseStudy,
    CausalGraph,
    Edge,
    Episode,
    MacroSnapshot,
    Node,
    ToolBundle,
    ToolError,
)

logger = logging.getLogger(__name__)


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
        return self.rebuttal.score - self.critique.score


@dataclass
class PipelineResult:
    """Full pipeline output. The graph is the merged + pruned DAG; portfolio
    impacts and case studies sit alongside for the demo to render."""

    graph: CausalGraph
    case_studies: list[CaseStudy] = field(default_factory=list)
    portfolio_impacts: list[PortfolioImpact] = field(default_factory=list)
    debates: dict[str, Debate] = field(default_factory=dict)
    comparator_results: dict[str, ComparatorResult] = field(default_factory=dict)
    run_id: Optional[str] = None


def run_adversarial_debate(
    graph: CausalGraph,
    *,
    include_nodes: bool = False,
    model: str = MODEL,
    client: Any = None,
    run_id: Optional[str] = None,
) -> dict[str, Debate]:
    """Stage 5. AdversaryAgent then DefenderAgent on every edge in the graph.

    Returns a dict keyed by target_id (edge.id, or node.id when include_nodes=True).
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


def run_pipeline(
    event: str,
    *,
    dry_run: bool = False,
    model: str = MODEL,
    client: Any = None,
    run_id: Optional[str] = None,
    today: Optional[date] = None,
    max_first_order: int = 4,
    max_analogs_per_node: int = 2,
    similarity_threshold: float = 0.3,
    tools: Optional[ToolBundle] = None,
) -> PipelineResult:
    """Stages 1, 2, 3, 5, 6, 7, 9. See CLAUDE.md 'Pipeline (end to end)'.

    Stages 4 (LogicVerifier) and 8 (second TreeBuilder pass) are skipped per
    the CLAUDE.md scope-cut order. Stage 10 (ScenarioAgent) is callable
    standalone but not wired into the main pipeline path.
    """
    if dry_run:
        print(f"[dry-run] event={event!r} model={model}")
        return PipelineResult(graph=CausalGraph(), run_id=run_id)

    if tools is None:
        tools = make_default_tools()
    today = today or date.today()
    run_id = run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # ------------------------------------------------------------------
    # Stage 1: First-order nodes from IdeaAgent
    # ------------------------------------------------------------------
    logger.info("Stage 1: IdeaAgent")
    first_order = idea.run(event, tools=tools, model=model, client=client)
    first_order = first_order[:max_first_order]
    if not first_order:
        logger.warning("Stage 1 produced no first-order nodes; aborting pipeline")
        return PipelineResult(graph=CausalGraph(), run_id=run_id)

    graph = _build_root_with_first_order(event, first_order)

    # ------------------------------------------------------------------
    # Stage 2 + 3: For each first-order node, find analogs and build subtrees
    # ------------------------------------------------------------------
    logger.info(
        "Stages 2+3: AnalogSearch and TreeBuilder on %d first-order nodes",
        len(first_order),
    )
    case_studies: list[CaseStudy] = []
    case_study_to_first_order: dict[str, str] = {}

    for fo_node in first_order:
        episodes = analog_search.run(
            fo_node, tools=tools, model=model, client=client, run_id=run_id
        )
        for ep in episodes[:max_analogs_per_node]:
            cs = _build_case_study_from_episode(ep, tools)
            if cs is None:
                continue
            cs = tree_builder.build_subtree(
                cs, tools=tools, model=model, run_id=run_id
            )
            case_studies.append(cs)
            case_study_to_first_order[cs.name] = fo_node.id

    # ------------------------------------------------------------------
    # Stage 5: Adversarial debate on trunk + each subtree
    # ------------------------------------------------------------------
    logger.info("Stage 5: Adversarial debate")
    all_debates = run_adversarial_debate(
        graph, model=model, client=client, run_id=run_id
    )
    for cs in case_studies:
        all_debates.update(
            run_adversarial_debate(
                cs.subtree, model=model, client=client, run_id=run_id
            )
        )

    # ------------------------------------------------------------------
    # Stage 6: Macro then-vs-now comparator
    # ------------------------------------------------------------------
    logger.info("Stage 6: MacroComparator")
    now_snapshot = _safe_macro_snapshot(today, tools)
    comparator_results: dict[str, ComparatorResult] = {}
    for cs in case_studies:
        result = macro_comparator.run(
            cs.macro_snapshot, now_snapshot, tools=tools, model=model
        )
        cs.similarity_score = result.similarity
        comparator_results[cs.name] = result

    # ------------------------------------------------------------------
    # Stage 7: Merge surviving subtrees into the trunk, then prune
    # ------------------------------------------------------------------
    logger.info("Stage 7: Merge subtrees + Pruner")
    case_study_subtree_roots: dict[str, str] = {}
    for cs in case_studies:
        if cs.similarity_score < similarity_threshold:
            continue
        fo_node_id = case_study_to_first_order.get(cs.name)
        if fo_node_id is None:
            continue
        subtree_root_id = cs.subtree.root or _first_node_id(cs.subtree)
        if subtree_root_id is None:
            continue
        case_study_subtree_roots[cs.name] = subtree_root_id

        for nid, n in cs.subtree.nodes.items():
            if nid not in graph.nodes:
                graph.nodes[nid] = n
        graph.edges.extend(cs.subtree.edges)
        graph.edges.append(
            Edge(
                src=fo_node_id,
                dst=subtree_root_id,
                mechanism=f"historical analog: {cs.name}",
                sensitivity=cs.similarity_score,
                confidence=cs.similarity_score,
            )
        )

    similarity_by_cs = {cs.name: cs.similarity_score for cs in case_studies}
    graph = pruner.run(
        graph,
        debates=all_debates,
        comparator=similarity_by_cs,
        case_study_subtree_roots=case_study_subtree_roots,
        similarity_threshold=similarity_threshold,
    )

    # ------------------------------------------------------------------
    # Stage 9: Portfolio mapping on terminals
    # ------------------------------------------------------------------
    logger.info("Stage 9: PortfolioAgent")
    terminals = _terminals(graph)
    portfolio_impacts = portfolio.run(
        terminals,
        tools=tools,
        model=model,
        graph=graph,
        seed_event=event,
        client=client,
    )

    return PipelineResult(
        graph=graph,
        case_studies=case_studies,
        portfolio_impacts=portfolio_impacts,
        debates=all_debates,
        comparator_results=comparator_results,
        run_id=run_id,
    )


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _build_root_with_first_order(event: str, first_order: list[Node]) -> CausalGraph:
    root = Node(id="root", label=event[:60], description=event, layer=0)
    nodes: dict[str, Node] = {"root": root}
    edges: list[Edge] = []
    for n in first_order:
        nodes[n.id] = n
        edges.append(
            Edge(
                src="root",
                dst=n.id,
                mechanism=f"event triggers {n.label}",
                sensitivity=0.5,
                confidence=0.4,  # weak prior; AdversaryAgent can downweight
            )
        )
    return CausalGraph(nodes=nodes, edges=edges, root="root")


def _build_case_study_from_episode(
    ep: Episode, tools: ToolBundle
) -> Optional[CaseStudy]:
    snap = _safe_macro_snapshot(ep.start, tools)
    name = ep.candidate_event or f"{ep.series_id} {ep.start.year}"
    return CaseStudy(
        name=name,
        date_range=(ep.start, ep.end),
        triggering_event=ep.candidate_event or f"Episode on {ep.series_id} starting {ep.start}",
        macro_snapshot=snap,
        similarity_score=0.0,
        subtree=CausalGraph(),
    )


def _safe_macro_snapshot(at: date, tools: ToolBundle) -> MacroSnapshot:
    if tools is None or tools.fred is None:
        return MacroSnapshot()
    try:
        snap = tools.fred.macro_snapshot(at)
    except Exception:
        return MacroSnapshot()
    if isinstance(snap, ToolError):
        return MacroSnapshot()
    return snap


def _terminals(graph: CausalGraph) -> list[Node]:
    has_outgoing = {e.src for e in graph.edges}
    return [
        n for nid, n in graph.nodes.items()
        if nid not in has_outgoing and nid != graph.root
    ]


def _first_node_id(g: CausalGraph) -> Optional[str]:
    return next(iter(g.nodes), None)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="policy-mapper")
    parser.add_argument("--event", required=True, help="plain English policy event")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    result = run_pipeline(args.event, dry_run=args.dry_run, model=args.model)
    if not args.dry_run:
        kept = sum(1 for cs in result.case_studies if cs.similarity_score >= 0.3)
        print(
            f"Built graph with {len(result.graph.nodes)} nodes, "
            f"{len(result.graph.edges)} edges."
        )
        print(
            f"Case studies: {len(result.case_studies)} ({kept} kept after similarity filter)"
        )
        print(f"Portfolio impacts: {len(result.portfolio_impacts)} asset classes")
        print(f"Run id: {result.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
