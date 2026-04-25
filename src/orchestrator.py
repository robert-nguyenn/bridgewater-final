from __future__ import annotations

import argparse
import copy
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

from src.agents import (
    adversary,
    analog_search,
    defender,
    idea,
    logic_verifier,
    macro_comparator,
    moderator as moderator_agent,
    portfolio,
    pruner,
    scenario,
    tree_builder,
)
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.agents.macro_comparator import ComparatorResult
from src.agents.moderator import ModeratorVerdict
from src.agents.portfolio import PortfolioImpact
from src.agents.scenario import TailScenario
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
class ProgressEvent:
    """Emitted by the orchestrator and pruner so a UI can stream progress.

    `kind` examples: stage_start, stage_complete, first_order_emitted,
    analog_found, case_study_built, debate_completed, comparator_result,
    subtree_attached, subtree_skipped, edge_pruned, subtree_dropped,
    pruning_summary, portfolio_complete.
    """

    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass
class Debate:
    target_id: str
    critique: Critique
    rebuttal: Rebuttal
    verdict: Optional[ModeratorVerdict] = None

    @property
    def survives(self) -> bool:
        """Moderator decision wins when present; otherwise defender wins ties."""
        if self.verdict is not None:
            return self.verdict.decision == "keep"
        return self.rebuttal.score >= self.critique.score

    @property
    def margin(self) -> float:
        return self.rebuttal.score - self.critique.score


@dataclass
class PipelineResult:
    """Full pipeline output. The graph is the merged + pruned DAG; portfolio
    impacts, case studies, and tail scenarios sit alongside for the demo."""

    graph: CausalGraph
    pre_prune_graph: Optional[CausalGraph] = None
    case_studies: list[CaseStudy] = field(default_factory=list)
    portfolio_impacts: list[PortfolioImpact] = field(default_factory=list)
    debates: dict[str, Debate] = field(default_factory=dict)
    comparator_results: dict[str, ComparatorResult] = field(default_factory=dict)
    case_study_to_first_order: dict[str, str] = field(default_factory=dict)
    tail_scenarios: list[TailScenario] = field(default_factory=list)
    progress_events: list[ProgressEvent] = field(default_factory=list)
    run_id: Optional[str] = None


def _make_logic_check(
    *, model: str, client: Any, run_id: Optional[str] = None
):
    """Factory that returns a `LogicCheck` callable for stage 3 sensitivity.

    For each (parent, candidate, mechanism), constructs a single-edge synthetic
    chain and asks LogicVerifier to assess local validity (passes 1, 2, 4 of
    the 4-pass CoT; pass 3 is trivial for a length-1 chain). Returns
    `{"ok": bool, "reason": str}` or None on failure (sensitivity falls back
    to its priors-only cap behavior in that case).
    """
    def check(parent: Node, candidate: Node, mechanism: str) -> Optional[dict]:
        try:
            edge = Edge(
                src=parent.id, dst=candidate.id, mechanism=mechanism,
                sensitivity=0.0, confidence=0.0,
            )
            nodes = {parent.id: parent, candidate.id: candidate}
            result = logic_verifier.run(
                [edge], nodes=nodes, model=model, client=client
            )
            return {"ok": result.ok, "reason": result.reason}
        except Exception as exc:
            logger.warning("logic check failed: %s", exc)
            return None
    return check


def run_adversarial_debate(
    graph: CausalGraph,
    *,
    include_nodes: bool = False,
    use_moderator: bool = True,
    model: str = MODEL,
    client: Any = None,
    run_id: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> dict[str, Debate]:
    """Stage 5. AdversaryAgent → DefenderAgent → optional ModeratorAgent on every edge.

    When `use_moderator=True` (default), an independent judge reads both
    transcripts and returns a final keep/drop decision plus a confidence
    adjustment. This decision drives pruning instead of the raw score margin.
    """
    debates: dict[str, Debate] = {}

    def emit(kind: str, message: str, **data: Any) -> None:
        if on_progress is not None:
            on_progress(ProgressEvent(kind=kind, message=message, data=data))

    for edge in graph.edges:
        critique = adversary.run(edge, nodes=graph.nodes, model=model, client=client)
        rebuttal = defender.run(
            edge, critique, nodes=graph.nodes, model=model, client=client
        )
        verdict: Optional[ModeratorVerdict] = None
        if use_moderator:
            verdict = moderator_agent.run(
                edge, critique, rebuttal,
                nodes=graph.nodes, model=model, client=client, run_id=run_id,
            )
            # Apply confidence_adjustment to the edge in-place (clamped).
            if verdict.confidence_adjustment != 0.0:
                edge.confidence = max(0.0, min(1.0, edge.confidence + verdict.confidence_adjustment))
        debates[edge.id] = Debate(
            target_id=edge.id, critique=critique, rebuttal=rebuttal, verdict=verdict
        )
        src_label = graph.nodes[edge.src].label if edge.src in graph.nodes else edge.src
        dst_label = graph.nodes[edge.dst].label if edge.dst in graph.nodes else edge.dst
        decision = verdict.decision if verdict else ("keep" if rebuttal.score >= critique.score else "drop")
        emit(
            "debate_complete",
            f"{src_label} -> {dst_label}: {decision} (adv {critique.score:.2f} / def {rebuttal.score:.2f}{f' / mod adj {verdict.confidence_adjustment:+.2f}' if verdict else ''})",
            edge_id=edge.id,
            adversary_score=critique.score,
            defender_score=rebuttal.score,
            margin=rebuttal.score - critique.score,
            moderator_decision=verdict.decision if verdict else None,
            moderator_adjustment=verdict.confidence_adjustment if verdict else 0.0,
        )

    if include_nodes:
        for node in graph.nodes.values():
            critique = adversary.run(node, nodes=graph.nodes, model=model, client=client)
            rebuttal = defender.run(
                node, critique, nodes=graph.nodes, model=model, client=client
            )
            verdict = None
            if use_moderator:
                verdict = moderator_agent.run(
                    node, critique, rebuttal,
                    nodes=graph.nodes, model=model, client=client, run_id=run_id,
                )
            debates[node.id] = Debate(
                target_id=node.id, critique=critique, rebuttal=rebuttal, verdict=verdict
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
    max_analogs_per_node: int = 4,
    similarity_threshold: float = 0.2,
    use_logic_verifier: bool = True,
    use_moderator: bool = True,
    run_scenarios: bool = True,
    portfolio_context: str = "",
    tools: Optional[ToolBundle] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> PipelineResult:
    """Stages 1, 2, 3, 5, 6, 7, 9. See ARCHITECTURE.md for the full walkthrough.

    Pass `on_progress` to receive a stream of ProgressEvents as the pipeline
    runs (case studies built, edges pruned, subtrees attached, etc.).
    """
    progress_events: list[ProgressEvent] = []

    def emit(kind: str, message: str, **data: Any) -> ProgressEvent:
        ev = ProgressEvent(kind=kind, message=message, data=data)
        progress_events.append(ev)
        if on_progress is not None:
            on_progress(ev)
        return ev

    if dry_run:
        emit("dry_run", f"event={event!r} model={model}")
        print(f"[dry-run] event={event!r} model={model}")
        return PipelineResult(graph=CausalGraph(), run_id=run_id, progress_events=progress_events)

    if tools is None:
        tools = make_default_tools()
    today = today or date.today()
    run_id = run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # LogicVerifier as primary out-of-sample signal. Threaded into stage 3.
    logic_check = (
        _make_logic_check(model=model, client=client, run_id=run_id)
        if use_logic_verifier else None
    )

    # ------------------------------------------------------------------
    # Stage 1
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 1: IdeaAgent generating first-order nodes", stage=1)
    first_order = idea.run(event, tools=tools, model=model, client=client)
    first_order = first_order[:max_first_order]
    if not first_order:
        emit("stage_failed", "Stage 1 produced no first-order nodes; aborting", stage=1)
        return PipelineResult(graph=CausalGraph(), run_id=run_id, progress_events=progress_events)

    for n in first_order:
        emit(
            "first_order_emitted",
            f"  {n.label} ({n.asset_class or 'unclassified'})",
            node_id=n.id,
            label=n.label,
            asset_class=n.asset_class,
        )

    graph = _build_root_with_first_order(event, first_order)
    emit(
        "stage_complete",
        f"Stage 1 done: {len(first_order)} first-order nodes attached to root",
        stage=1,
        n_nodes=len(graph.nodes),
        n_edges=len(graph.edges),
        trunk=copy.deepcopy(graph),
    )

    # ------------------------------------------------------------------
    # Stages 2 + 3
    # ------------------------------------------------------------------
    emit(
        "stage_start",
        f"Stages 2+3: AnalogSearch and TreeBuilder on {len(first_order)} first-order nodes",
        stage=2,
    )
    case_studies: list[CaseStudy] = []
    case_study_to_first_order: dict[str, str] = {}

    for fo_node in first_order:
        emit(
            "analog_search_start",
            f"  AnalogSearch for {fo_node.label!r}",
            node_id=fo_node.id,
            label=fo_node.label,
        )
        episodes = analog_search.run(
            fo_node, tools=tools, model=model, client=client, run_id=run_id
        )
        chosen = episodes[:max_analogs_per_node]
        emit(
            "analog_search_complete",
            f"    found {len(episodes)} episodes, taking top {len(chosen)}",
            node_id=fo_node.id,
            n_found=len(episodes),
            n_taken=len(chosen),
        )

        for ep in chosen:
            cs = _build_case_study_from_episode(ep, tools)
            if cs is None:
                continue
            emit(
                "case_study_started",
                f"    Building subtree for case study {cs.name!r} ({cs.date_range[0]} to {cs.date_range[1]})",
                case_study_id=cs.id,
                name=cs.name,
                first_order_id=fo_node.id,
            )
            cs = tree_builder.build_subtree(
                cs, tools=tools, model=model,
                logic_check=logic_check, run_id=run_id,
            )
            case_studies.append(cs)
            case_study_to_first_order[cs.id] = fo_node.id
            emit(
                "case_study_built",
                f"      subtree: {len(cs.subtree.nodes)} nodes, {len(cs.subtree.edges)} edges",
                case_study_id=cs.id,
                name=cs.name,
                first_order_id=fo_node.id,
                first_order_label=fo_node.label,
                n_nodes=len(cs.subtree.nodes),
                n_edges=len(cs.subtree.edges),
                subtree=copy.deepcopy(cs.subtree),
            )

    emit(
        "stage_complete",
        f"Stages 2+3 done: {len(case_studies)} case-study subtrees built",
        stage=3,
        n_case_studies=len(case_studies),
    )

    # ------------------------------------------------------------------
    # Stage 5: Adversarial debate
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 5: Adversarial debate on trunk + each subtree", stage=5)
    all_debates = run_adversarial_debate(
        graph, model=model, client=client, run_id=run_id,
        on_progress=on_progress, use_moderator=use_moderator,
    )
    for cs in case_studies:
        all_debates.update(
            run_adversarial_debate(
                cs.subtree, model=model, client=client, run_id=run_id,
                on_progress=on_progress, use_moderator=use_moderator,
            )
        )
    n_kept = sum(1 for d in all_debates.values() if d.survives)
    emit(
        "stage_complete",
        f"Stage 5 done: {len(all_debates)} debates ({n_kept} edges defender-favored)",
        stage=5,
        n_debates=len(all_debates),
        n_defender_wins=n_kept,
    )

    # ------------------------------------------------------------------
    # Stage 6: MacroComparator
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 6: MacroComparator (then vs now)", stage=6)
    now_snapshot = _safe_macro_snapshot(today, tools)
    comparator_results: dict[str, ComparatorResult] = {}
    for cs in case_studies:
        result = macro_comparator.run(
            cs.macro_snapshot, now_snapshot, tools=tools, model=model
        )
        cs.similarity_score = result.similarity
        comparator_results[cs.id] = result
        emit(
            "comparator_result",
            f"  {cs.name}: similarity {result.similarity:.2f} (diverging: {', '.join(result.diverging_dimensions) or '-'})",
            case_study_id=cs.id,
            name=cs.name,
            similarity=result.similarity,
            diverging=result.diverging_dimensions,
        )
    emit("stage_complete", "Stage 6 done", stage=6)

    # ------------------------------------------------------------------
    # Stage 7: Merge surviving subtrees + prune
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 7: Merge surviving subtrees + Pruner", stage=7)
    case_study_subtree_roots: dict[str, str] = {}
    n_attached = 0
    for cs in case_studies:
        if cs.similarity_score < similarity_threshold:
            emit(
                "subtree_skipped",
                f"  Skip {cs.name!r}: similarity {cs.similarity_score:.2f} < threshold {similarity_threshold}",
                case_study_id=cs.id,
                name=cs.name,
                similarity=cs.similarity_score,
            )
            continue
        fo_node_id = case_study_to_first_order.get(cs.id)
        if fo_node_id is None:
            continue
        subtree_root_id = cs.subtree.root or _first_node_id(cs.subtree)
        if subtree_root_id is None:
            continue
        case_study_subtree_roots[cs.id] = subtree_root_id

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
        n_attached += 1
        emit(
            "subtree_attached",
            f"  Attach {cs.name!r} to first-order node {fo_node_id} (similarity {cs.similarity_score:.2f})",
            case_study_id=cs.id,
            name=cs.name,
            similarity=cs.similarity_score,
            first_order_id=fo_node_id,
            n_nodes_added=len(cs.subtree.nodes),
            n_edges_added=len(cs.subtree.edges) + 1,
        )

    pre_prune_graph = copy.deepcopy(graph)
    emit(
        "merged_graph_built",
        f"  Merged graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges ({n_attached} subtrees attached)",
        n_nodes=len(graph.nodes),
        n_edges=len(graph.edges),
        merged_graph=copy.deepcopy(pre_prune_graph),
    )

    similarity_by_cs = {cs.id: cs.similarity_score for cs in case_studies}

    def _on_pruner_event(ev: dict[str, Any]) -> None:
        # Pop "kind" so **ev doesn't collide with emit's positional `kind` arg.
        kind = ev.pop("kind", "")
        if kind == "edge_pruned":
            emit(
                "edge_pruned",
                f"    drop edge {ev.get('src_label')!r} -> {ev.get('dst_label')!r}: "
                f"adversary {ev.get('adversary_score', 0):.2f} > defender {ev.get('defender_score', 0):.2f}",
                **ev,
            )
        elif kind == "subtree_dropped":
            emit(
                "subtree_dropped",
                f"    drop subtree {ev.get('case_study')!r}: similarity {ev.get('similarity', 0):.2f}",
                **ev,
            )
        elif kind == "node_orphaned":
            emit(
                "node_orphaned",
                f"    orphan {ev.get('label')!r}: {ev.get('reason')}",
                **ev,
            )
        elif kind == "pruning_summary":
            emit(
                "pruning_summary",
                f"  Pruner: {ev.get('edges_dropped', 0)} edges dropped, {ev.get('nodes_dropped', 0)} nodes dropped",
                **ev,
            )

    graph = pruner.run(
        graph,
        debates=all_debates,
        comparator=similarity_by_cs,
        case_study_subtree_roots=case_study_subtree_roots,
        similarity_threshold=similarity_threshold,
        on_event=_on_pruner_event,
    )
    emit(
        "stage_complete",
        f"Stage 7 done: pruned graph {len(graph.nodes)} nodes, {len(graph.edges)} edges",
        stage=7,
        n_nodes=len(graph.nodes),
        n_edges=len(graph.edges),
        pruned_graph=copy.deepcopy(graph),
    )

    # ------------------------------------------------------------------
    # Stage 9: Portfolio
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 9: PortfolioAgent on terminal nodes", stage=9)
    terminals = _terminals(graph)
    portfolio_impacts = portfolio.run(
        terminals,
        tools=tools,
        model=model,
        graph=graph,
        seed_event=event,
        portfolio_context=portfolio_context,
        client=client,
    )
    for impact in portfolio_impacts:
        emit(
            "portfolio_impact_emitted",
            f"  {impact.asset_class}: {impact.direction} ({impact.magnitude_label}) - {impact.summary}",
            asset_class=impact.asset_class,
            direction=impact.direction,
            magnitude_label=impact.magnitude_label,
            confidence=impact.confidence,
        )
    emit(
        "stage_complete",
        f"Stage 9 done: {len(portfolio_impacts)} portfolio impacts",
        stage=9,
        n_impacts=len(portfolio_impacts),
    )

    # ------------------------------------------------------------------
    # Stage 10: ScenarioAgent (stretch). Tail policy scenarios anchored to
    # Kalshi prediction-market prices. Each scenario's `feedback_event` can
    # be re-run through this pipeline as a new seed.
    # ------------------------------------------------------------------
    tail_scenarios: list[TailScenario] = []
    if run_scenarios:
        emit("stage_start", "Stage 10: ScenarioAgent (tail policy scenarios)", stage=10)
        try:
            tail_scenarios = scenario.run(
                seed_event=event,
                tools=tools,
                model=model,
                client=client,
            )
            for s in tail_scenarios:
                emit(
                    "scenario_emitted",
                    f"  {s.text} (p={s.probability:.2f}, source={s.probability_source})",
                    text=s.text,
                    probability=s.probability,
                    probability_source=s.probability_source,
                    policy_axis=s.policy_axis,
                    horizon_days=s.time_horizon_days,
                )
            emit(
                "stage_complete",
                f"Stage 10 done: {len(tail_scenarios)} tail scenarios",
                stage=10,
                n_scenarios=len(tail_scenarios),
            )
        except Exception as exc:
            logger.warning("Stage 10 ScenarioAgent failed: %s", exc)
            emit(
                "stage_failed",
                f"Stage 10 failed: {type(exc).__name__}: {exc}",
                stage=10,
                error=str(exc),
            )

    return PipelineResult(
        graph=graph,
        pre_prune_graph=pre_prune_graph,
        case_studies=case_studies,
        portfolio_impacts=portfolio_impacts,
        debates=all_debates,
        comparator_results=comparator_results,
        case_study_to_first_order=case_study_to_first_order,
        tail_scenarios=tail_scenarios,
        progress_events=progress_events,
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
                confidence=0.4,
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
    parser.add_argument("--verbose", "-v", action="store_true", help="print progress events live")
    args = parser.parse_args(argv)

    on_progress = (lambda ev: print(f"[{ev.kind}] {ev.message}")) if args.verbose else None
    result = run_pipeline(
        args.event,
        dry_run=args.dry_run,
        model=args.model,
        on_progress=on_progress,
    )
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
