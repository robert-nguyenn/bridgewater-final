from __future__ import annotations

import argparse
import copy
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from src.agents._common import extract_json
from src.agents.adversary import Critique
from src.agents.defender import Rebuttal
from src.agents.macro_comparator import ComparatorResult, LinkApplicability
from src.agents.moderator import ModeratorVerdict
from src.agents.portfolio import PortfolioImpact
from src.agents.scenario import TailScenario
import json as _json
from src.config import MODEL, MODEL_FAST
from src.tools import make_default_tools, validate_citations
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
    case_study_to_first_order_ids: dict[str, list[str]] = field(default_factory=dict)  # multi-parent: cs_id → all fo_node_ids that claimed this analog
    tail_scenarios: list[TailScenario] = field(default_factory=list)
    chain_verifications: dict[str, Any] = field(default_factory=dict)
    citation_validations: dict[str, str] = field(default_factory=dict)
    link_applicabilities: dict[str, Any] = field(default_factory=dict)
    progress_events: list[ProgressEvent] = field(default_factory=list)
    run_id: Optional[str] = None




DEFAULT_DEBATE_WORKERS = 4
DEFAULT_SUBTREE_WORKERS = 4
ANALOG_OVERFETCH_FACTOR = 2  # over-fetch this much per first-order node so macro filter + per-fo trim still yield max_analogs_per_node survivors


def run_adversarial_debate(
    graph: CausalGraph,
    *,
    only_edges: Optional[list[Edge]] = None,
    include_nodes: bool = False,
    use_moderator: bool = True,
    model: str = MODEL,
    client: Any = None,
    run_id: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    max_workers: int = DEFAULT_DEBATE_WORKERS,
) -> dict[str, Debate]:
    """AdversaryAgent → DefenderAgent → optional ModeratorAgent on edges.

    The adv→def→mod chain stays sequential within each edge (the moderator
    needs both transcripts), but multiple edges debate concurrently via a
    ThreadPoolExecutor. Anthropic HTTP calls release the GIL so this is real
    parallelism.

    When `only_edges` is None, debates every edge in the graph (the original
    stage 5 behavior). When provided, debates that subset only.

    `graph` is required for node context (labels/descriptions) in agent prompts.
    """
    debates: dict[str, Debate] = {}
    debates_lock = threading.Lock()

    def emit(kind: str, message: str, **data: Any) -> None:
        if on_progress is not None:
            on_progress(ProgressEvent(kind=kind, message=message, data=data))

    target_edges = only_edges if only_edges is not None else graph.edges
    if not target_edges and not include_nodes:
        return debates

    def _debate_one_edge(edge: Edge) -> None:
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
            if verdict.confidence_adjustment != 0.0:
                # Each edge object is exclusive to this future, so the in-place
                # mutation is safe without a lock.
                edge.confidence = max(0.0, min(1.0, edge.confidence + verdict.confidence_adjustment))
        d = Debate(target_id=edge.id, critique=critique, rebuttal=rebuttal, verdict=verdict)
        with debates_lock:
            debates[edge.id] = d
        src_label = graph.nodes[edge.src].label if edge.src in graph.nodes else edge.src
        dst_label = graph.nodes[edge.dst].label if edge.dst in graph.nodes else edge.dst
        decision = verdict.decision if verdict else ("keep" if rebuttal.score >= critique.score else "drop")
        emit(
            "debate_complete",
            f"{src_label} -> {dst_label}: {decision} (adv {critique.score:.2f} / def {rebuttal.score:.2f}"
            f"{f' / mod adj {verdict.confidence_adjustment:+.2f}' if verdict else ''})",
            edge_id=edge.id,
            adversary_score=critique.score,
            defender_score=rebuttal.score,
            margin=rebuttal.score - critique.score,
            moderator_decision=verdict.decision if verdict else None,
            moderator_adjustment=verdict.confidence_adjustment if verdict else 0.0,
        )

    if target_edges:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_debate_one_edge, e) for e in target_edges]
            for fut in as_completed(futures):
                fut.result()  # propagate exceptions

    if include_nodes:
        # Node-level debates also run concurrently.
        def _debate_one_node(node: Node) -> None:
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
            d = Debate(
                target_id=node.id, critique=critique, rebuttal=rebuttal, verdict=verdict
            )
            with debates_lock:
                debates[node.id] = d

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_debate_one_node, n) for n in graph.nodes.values()]
            for fut in as_completed(futures):
                fut.result()

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
    # Stages 2 + 3 — parallel
    # ------------------------------------------------------------------
    emit(
        "stage_start",
        f"Stages 2+3: AnalogSearch and TreeBuilder on {len(first_order)} first-order nodes",
        stage=2,
    )
    case_studies: list[CaseStudy] = []
    # case_study_to_first_order and comparator_results are populated below
    # during the pre-build macro filter so we can drop irrelevant case studies
    # before paying for tree-builder + debate calls.
    all_debates: dict[str, Debate] = {}  # collected from per-layer debates + stage 5 trunk
    state_lock = threading.Lock()  # guards case_studies appends
    debates_lock = threading.Lock()  # guards all_debates updates

    # 2a) Run AnalogSearch in parallel across first-order nodes. We over-fetch
    # by ANALOG_OVERFETCH_FACTOR so that after cross-FO dedup and the pre-build
    # macro similarity filter, each node still has `max_analogs_per_node`
    # survivors to attach. Per-FO trimming happens below after the filter.
    overfetch_k = max(max_analogs_per_node * ANALOG_OVERFETCH_FACTOR, max_analogs_per_node)

    def _search_for_fo(fo_node: Node) -> tuple[Node, list]:
        emit(
            "analog_search_start",
            f"  AnalogSearch for {fo_node.label!r} (over-fetching {overfetch_k} candidates)",
            node_id=fo_node.id,
            label=fo_node.label,
        )
        eps = analog_search.run(
            fo_node, tools=tools, model=model, client=client, run_id=run_id,
            k=overfetch_k,
        )
        emit(
            "analog_search_complete",
            f"    found {len(eps)} episodes for {fo_node.label!r}",
            node_id=fo_node.id,
            n_found=len(eps),
        )
        return fo_node, eps

    with ThreadPoolExecutor(max_workers=DEFAULT_SUBTREE_WORKERS) as executor:
        analog_futures = [executor.submit(_search_for_fo, fo) for fo in first_order]
        analog_results: list[tuple[Node, list]] = [
            f.result() for f in as_completed(analog_futures)
        ]

    # 2b) Cross-first-order analog dedup via one batch LLM call. Each episode
    # carries its rank within the originating fo_node (0 = best), used below
    # to per-FO trim after the macro filter so each node ends up with
    # `max_analogs_per_node` surviving claims.
    flat_pairs_with_rank: list[tuple[Node, int, Episode]] = [
        (fo, rank, ep)
        for fo, eps in analog_results
        for rank, ep in enumerate(eps)
    ]
    flat_eps = [ep for _, _, ep in flat_pairs_with_rank]
    group_ids = _llm_dedup_episodes(
        flat_eps, model=MODEL_FAST, client=client, run_id=run_id
    )
    # Each group: {"episode": Episode, "fo_claims": [(Node, rank), ...]}
    # If the same fo_node claims the same group multiple times, keep the best rank.
    episode_groups: dict[str, dict[str, Any]] = {}
    for (fo_node, rank, ep), gid in zip(flat_pairs_with_rank, group_ids):
        group = episode_groups.setdefault(gid, {"episode": ep, "fo_claims": []})
        existing = next(
            (c for c in group["fo_claims"] if c[0].id == fo_node.id), None
        )
        if existing is None:
            group["fo_claims"].append((fo_node, rank))
        elif rank < existing[1]:
            group["fo_claims"].remove(existing)
            group["fo_claims"].append((fo_node, rank))

    n_total_episode_appearances = sum(len(eps) for _, eps in analog_results)
    n_unique_groups = len(episode_groups)
    n_shared = sum(1 for g in episode_groups.values() if len(g["fo_claims"]) > 1)
    if n_total_episode_appearances > n_unique_groups:
        emit(
            "analog_dedup_summary",
            f"  Cross-FO dedup: {n_total_episode_appearances} episode appearances → "
            f"{n_unique_groups} unique case studies ({n_shared} shared by ≥2 first-order nodes)",
            n_appearances=n_total_episode_appearances,
            n_unique=n_unique_groups,
            n_shared=n_shared,
        )

    # 2c) Per-unique-case-study seed + macro similarity filter *before* tree
    # building. The filter is structural (zero LLM calls). Case studies whose
    # regime is too far from today (similarity < threshold) are dropped pre-
    # build, saving the entire stage 3 cost for irrelevant analogs.
    # 2c) Per-unique-case-study seed + macro similarity filter. Survivors then
    # go through per-FO trim (2d) before subtree build kicks off in stage 3.
    now_snapshot = _safe_macro_snapshot(today, tools)
    comparator_results: dict[str, ComparatorResult] = {}
    # passed_filter: cs_id → (CaseStudy, [(fo_node, rank), ...])
    passed_filter: dict[str, tuple[CaseStudy, list[tuple[Node, int]]]] = {}
    n_dropped_pre_build = 0
    for gid, group in episode_groups.items():
        ep = group["episode"]
        fo_claims: list[tuple[Node, int]] = group["fo_claims"]
        cs = _build_case_study_from_episode(ep, tools)
        if cs is None:
            continue
        cmp_result = macro_comparator.run(
            cs.macro_snapshot, now_snapshot, tools=tools, model=model
        )
        cs.similarity_score = cmp_result.similarity
        comparator_results[cs.id] = cmp_result
        shared_suffix = (
            f" (shared by {len(fo_claims)} first-order nodes: "
            f"{', '.join(repr(n.label) for n, _ in fo_claims)})"
            if len(fo_claims) > 1 else ""
        )
        emit(
            "comparator_result",
            f"    {cs.name!r}: similarity {cs.similarity_score:.2f} "
            f"(diverging: {', '.join(cmp_result.diverging_dimensions) or '-'}){shared_suffix}",
            case_study_id=cs.id,
            name=cs.name,
            similarity=cs.similarity_score,
            diverging=cmp_result.diverging_dimensions,
            n_first_order_claimers=len(fo_claims),
        )
        if cs.similarity_score < similarity_threshold:
            n_dropped_pre_build += 1
            emit(
                "case_study_dropped_pre_build",
                f"    Skip {cs.name!r}: similarity {cs.similarity_score:.2f} < threshold {similarity_threshold}; not building subtree",
                case_study_id=cs.id,
                name=cs.name,
                similarity=cs.similarity_score,
            )
            continue
        passed_filter[cs.id] = (cs, fo_claims)

    # 2d) Per-FO trim. Each first-order node keeps only its top
    # `max_analogs_per_node` surviving case studies (ranked by the original
    # AnalogSearch order, where 0 = best). A case study with all claims
    # trimmed away is dropped entirely; one with at least one surviving claim
    # attaches to those fo_nodes only. This guarantees each first-order node
    # ends up with the correct analog count after macro filter + dedup.
    fo_ranked: dict[str, list[tuple[int, str]]] = {}
    for cs_id, (_cs, fo_claims) in passed_filter.items():
        for fo_node, rank in fo_claims:
            fo_ranked.setdefault(fo_node.id, []).append((rank, cs_id))

    trimmed_claims: dict[str, list[str]] = {}  # cs_id → fo_ids that still claim
    n_trimmed_excess = 0
    for fo_id, ranked in fo_ranked.items():
        ranked.sort()  # by rank ascending
        kept = ranked[:max_analogs_per_node]
        n_trimmed_excess += max(0, len(ranked) - max_analogs_per_node)
        for _, cs_id in kept:
            trimmed_claims.setdefault(cs_id, []).append(fo_id)

    # Build the final case-study mappings + build_tasks, only for case
    # studies with at least one surviving claim after per-FO trim.
    build_tasks: list[tuple[Node, CaseStudy]] = []
    case_study_to_first_order: dict[str, str] = {}
    case_study_to_first_order_ids: dict[str, list[str]] = {}
    n_trimmed_dropped = 0
    for cs_id, (cs, fo_claims) in passed_filter.items():
        surviving_fo_ids = trimmed_claims.get(cs_id, [])
        if not surviving_fo_ids:
            n_trimmed_dropped += 1
            emit(
                "case_study_trimmed_per_fo_cap",
                f"    Trim {cs.name!r}: all claiming first-order nodes already have "
                f"{max_analogs_per_node} better-ranked analogs",
                case_study_id=cs.id,
                name=cs.name,
            )
            continue
        case_study_to_first_order_ids[cs.id] = surviving_fo_ids
        case_study_to_first_order[cs.id] = surviving_fo_ids[0]
        primary_fo = next(
            (n for n, _ in fo_claims if n.id == surviving_fo_ids[0]),
            fo_claims[0][0],
        )
        emit(
            "case_study_started",
            f"    Queued subtree {cs.name!r} under {primary_fo.label!r} "
            f"(claimed by {len(surviving_fo_ids)} first-order nodes after trim)",
            case_study_id=cs.id,
            name=cs.name,
            first_order_id=primary_fo.id,
            similarity=cs.similarity_score,
            n_first_order_claimers=len(surviving_fo_ids),
        )
        build_tasks.append((primary_fo, cs))

    if n_dropped_pre_build or n_trimmed_excess or n_trimmed_dropped:
        # Per-FO survival counts after both filters.
        per_fo_counts = {
            fo_id: min(len([cs_id for _, cs_id in ranked if cs_id in trimmed_claims and fo_id in trimmed_claims[cs_id]]), max_analogs_per_node)
            for fo_id, ranked in fo_ranked.items()
        }
        per_fo_summary = ", ".join(
            f"{fid[:8]}={count}" for fid, count in per_fo_counts.items()
        )
        emit(
            "pre_build_filter_summary",
            f"  Pre-build pipeline: {n_dropped_pre_build} dropped by macro filter, "
            f"{n_trimmed_excess} excess claims trimmed (cap {max_analogs_per_node} per first-order node), "
            f"{n_trimmed_dropped} case studies fully dropped after trim. "
            f"{len(build_tasks)} survive to stage 3. Per-FO counts: {per_fo_summary}",
            n_dropped_macro=n_dropped_pre_build,
            n_trimmed_excess=n_trimmed_excess,
            n_trimmed_dropped=n_trimmed_dropped,
            n_kept=len(build_tasks),
            per_fo_counts=per_fo_counts,
        )

    # 3) Build subtrees in parallel. Each subtree's per-layer debate runs its
    # own ThreadPoolExecutor (small max_workers within), so the outer pool
    # stays modest to avoid thread explosion. Total threads ≈ outer × inner.
    def _build_one_subtree(fo_node: Node, cs: CaseStudy) -> tuple[Node, CaseStudy]:
        _cs_id_capt = cs.id
        _cs_name_capt = cs.name
        _fo_id_capt = fo_node.id
        _fo_label_capt = fo_node.label

        def _on_subtree_event(sub_ev: dict[str, Any]) -> None:
            sub_kind = sub_ev.get("kind", "")
            forwarded = {k: v for k, v in sub_ev.items() if k != "kind"}
            forwarded.setdefault("case_study_id", _cs_id_capt)
            forwarded.setdefault("name", _cs_name_capt)
            forwarded.setdefault("first_order_id", _fo_id_capt)
            forwarded.setdefault("first_order_label", _fo_label_capt)
            if sub_kind == "subtree_candidate_added":
                emit(
                    "subtree_candidate_added",
                    f"      + L{forwarded.get('layer')} {forwarded.get('candidate_label', '')!r} "
                    f"under {forwarded.get('parent_label', '')!r}",
                    **forwarded,
                )
            elif sub_kind == "subtree_candidate_merged":
                emit(
                    "subtree_candidate_merged",
                    f"      ↪ L{forwarded.get('layer')} reuse {forwarded.get('existing_label', '')!r} "
                    f"under {forwarded.get('parent_label', '')!r} (multi-parent)",
                    **forwarded,
                )
            elif sub_kind == "subtree_layer_start":
                emit(
                    "subtree_layer_start",
                    f"    layer {forwarded.get('layer')} of {_cs_name_capt!r}",
                    **forwarded,
                )
            elif sub_kind == "subtree_init":
                emit(
                    "subtree_init",
                    f"    initialized {_cs_name_capt!r}",
                    **forwarded,
                )

        def _on_layer_complete(
            subtree_graph: CausalGraph,
            layer_edges: list[Edge],
            layer: int,
        ) -> set[str]:
            if not layer_edges:
                return set()
            emit(
                "subtree_layer_debate_start",
                f"    debating {len(layer_edges)} new edges at layer {layer} of {_cs_name_capt!r}",
                case_study_id=_cs_id_capt,
                layer=layer,
                n_edges=len(layer_edges),
            )
            # Inner pool is small to avoid thread explosion under outer pool.
            layer_debates = run_adversarial_debate(
                subtree_graph,
                only_edges=layer_edges,
                model=model,
                client=client,
                run_id=run_id,
                on_progress=on_progress,
                use_moderator=use_moderator,
                max_workers=2,
            )
            with debates_lock:
                all_debates.update(layer_debates)
            drop_ids = {
                eid for eid, d in layer_debates.items() if not d.survives
            }
            emit(
                "subtree_layer_debate_complete",
                f"    debate done at layer {layer}: {len(drop_ids)}/{len(layer_edges)} dropped",
                case_study_id=_cs_id_capt,
                layer=layer,
                n_dropped=len(drop_ids),
                n_kept=len(layer_edges) - len(drop_ids),
            )
            return drop_ids

        cs_built = tree_builder.build_subtree(
            cs, tools=tools, model=model, run_id=run_id,
            on_progress=_on_subtree_event,
            on_layer_complete=_on_layer_complete,
        )
        emit(
            "case_study_built",
            f"      subtree {_cs_name_capt!r}: {len(cs_built.subtree.nodes)} nodes, {len(cs_built.subtree.edges)} edges",
            case_study_id=cs_built.id,
            name=cs_built.name,
            first_order_id=_fo_id_capt,
            first_order_label=_fo_label_capt,
            n_nodes=len(cs_built.subtree.nodes),
            n_edges=len(cs_built.subtree.edges),
            subtree=copy.deepcopy(cs_built.subtree),
        )
        return fo_node, cs_built

    with ThreadPoolExecutor(max_workers=DEFAULT_SUBTREE_WORKERS) as executor:
        subtree_futures = [
            executor.submit(_build_one_subtree, fo, cs) for fo, cs in build_tasks
        ]
        for fut in as_completed(subtree_futures):
            try:
                fo_node_done, cs_done = fut.result()
            except Exception as exc:
                logger.warning("subtree build failed: %s", exc)
                continue
            with state_lock:
                case_studies.append(cs_done)
                case_study_to_first_order[cs_done.id] = fo_node_done.id

    emit(
        "stage_complete",
        f"Stages 2+3 done: {len(case_studies)} case-study subtrees built (parallel)",
        stage=3,
        n_case_studies=len(case_studies),
    )

    # ------------------------------------------------------------------
    # Stage 5: Adversarial debate on the TRUNK only.
    # Subtree edges were already debated layer-by-layer in stage 3 via the
    # on_layer_complete callback, which dropped losers before each next
    # layer expanded.
    # ------------------------------------------------------------------
    emit(
        "stage_start",
        "Stage 5: Adversarial debate on trunk (subtrees already debated per-layer)",
        stage=5,
    )
    trunk_debates = run_adversarial_debate(
        graph, model=model, client=client, run_id=run_id,
        on_progress=on_progress, use_moderator=use_moderator,
    )
    all_debates.update(trunk_debates)
    n_kept = sum(1 for d in all_debates.values() if d.survives)
    emit(
        "stage_complete",
        f"Stage 5 done: {len(all_debates)} total debates ({n_kept} survived)",
        stage=5,
        n_debates=len(all_debates),
        n_defender_wins=n_kept,
    )

    # ------------------------------------------------------------------
    # Stage 6: MacroComparator
    # ------------------------------------------------------------------
    emit(
        "stage_start",
        "Stage 6: MacroComparator (already computed pre-build to filter case studies)",
        stage=6,
    )
    # `now_snapshot` and `comparator_results` are already populated during the
    # pre-build macro filter (right after AnalogSearch). Re-bind locals here
    # for downstream code clarity; comparator_results is fully populated.
    emit(
        "stage_complete",
        f"Stage 6 done (no work — comparator ran pre-build, "
        f"{len(comparator_results)} case studies have similarity scores, "
        f"{len(case_studies)} survived to here)",
        stage=6,
        n_comparator=len(comparator_results),
        n_built=len(case_studies),
    )

    # ------------------------------------------------------------------
    # Stage 7: Merge surviving subtrees + prune.
    # The cs_root (the historical triggering event node) is dropped during
    # merge; its first-layer children become direct children of the
    # corresponding first-order node via bridge edges. This avoids a
    # synthetic intermediate that the chain verifier would interpret as a
    # forward causal step.
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 7: Per-bridge applies-today check + merge + Pruner", stage=7)
    # Step 1: collect bridge candidates across all kept case studies.
    # When a case study is shared (claimed by multiple first-order nodes via
    # cross-FO analog dedup), each first-order node gets its own per-bridge
    # applies-today check.
    bridge_candidates: list[dict[str, Any]] = []
    case_study_meta: dict[str, dict[str, Any]] = {}
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
        fo_node_ids = case_study_to_first_order_ids.get(cs.id) or (
            [case_study_to_first_order[cs.id]] if cs.id in case_study_to_first_order else []
        )
        fo_nodes = [graph.nodes[fid] for fid in fo_node_ids if fid in graph.nodes]
        if not fo_nodes:
            continue
        cs_root_id = cs.subtree.root or _first_node_id(cs.subtree)
        if cs_root_id is None:
            continue
        first_layer_dst_ids = [e.dst for e in cs.subtree.edges if e.src == cs_root_id]
        if not first_layer_dst_ids:
            continue
        case_study_meta[cs.id] = {
            "fo_node_ids": [n.id for n in fo_nodes],
            "fo_nodes": fo_nodes,
            "cs_root_id": cs_root_id,
        }
        for fo_node in fo_nodes:
            for child_id in first_layer_dst_ids:
                child_node = cs.subtree.nodes.get(child_id)
                if child_node is None:
                    continue
                cs_edge = next(
                    (e for e in cs.subtree.edges if e.src == cs_root_id and e.dst == child_id),
                    None,
                )
                mechanism = (
                    cs_edge.mechanism if cs_edge else f"{cs.name} → {child_node.label}"
                )
                bridge_candidates.append({
                    "cs_id": cs.id,
                    "cs_name": cs.name,
                    "cs_macro": cs.macro_snapshot,
                    "cs_similarity": cs.similarity_score,
                    "fo_node_id": fo_node.id,
                    "fo_node_label": fo_node.label,
                    "child_id": child_id,
                    "child_label": child_node.label,
                    "mechanism": mechanism,
                })

    # Step 2: parallel per-bridge applies-today check via MacroComparator.
    # Each check picks relevant macro indices for the linkage and measures
    # then-vs-now distance on those indices alone.
    link_applicabilities: dict[str, LinkApplicability] = {}
    if bridge_candidates:
        emit(
            "applies_today_start",
            f"  Applies-today: checking {len(bridge_candidates)} bridge candidates",
            n_candidates=len(bridge_candidates),
        )

        def _check_bridge(cand: dict[str, Any]) -> tuple[dict[str, Any], LinkApplicability]:
            return cand, macro_comparator.link_applicability(
                parent_label=cand["fo_node_label"],
                child_label=cand["child_label"],
                mechanism=cand["mechanism"],
                then_snapshot=cand["cs_macro"],
                now_snapshot=now_snapshot,
                model=MODEL_FAST,
                client=client,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            applicabilities = list(executor.map(_check_bridge, bridge_candidates))

        n_kept = sum(1 for _, a in applicabilities if a.applies)
        emit(
            "applies_today_complete",
            f"  Applies-today: {n_kept}/{len(applicabilities)} bridges applicable",
            n_applicable=n_kept,
            n_total=len(applicabilities),
        )
    else:
        applicabilities = []

    # Group by case study for merging.
    applies_by_cs: dict[str, list[tuple[dict[str, Any], LinkApplicability]]] = {}
    for cand, app in applicabilities:
        applies_by_cs.setdefault(cand["cs_id"], []).append((cand, app))
        link_applicabilities[f"{cand['cs_id']}::{cand['child_id']}"] = app

    # Step 3: merge surviving subtrees, attaching only bridges that apply today.
    case_study_subtree_nodes: dict[str, set[str]] = {}
    n_attached = 0
    for cs in case_studies:
        meta = case_study_meta.get(cs.id)
        if meta is None:
            continue
        fo_nodes: list[Node] = meta["fo_nodes"]
        cs_root_id = meta["cs_root_id"]

        kept_acts = [(c, a) for c, a in applies_by_cs.get(cs.id, []) if a.applies]
        if not kept_acts:
            emit(
                "subtree_skipped",
                f"  Skip {cs.name!r}: no first-layer children apply today (all {len(applies_by_cs.get(cs.id, []))} bridges failed applicability)",
                case_study_id=cs.id,
                name=cs.name,
                similarity=cs.similarity_score,
            )
            continue

        # Layer rebase uses the deepest fo_node's layer so subtree nodes sit
        # below all of their multi-parent first-order claimers.
        max_fo_layer = max((n.layer or 1) for n in fo_nodes)

        attached_node_ids: set[str] = set()
        for nid, n in cs.subtree.nodes.items():
            if nid == cs_root_id:
                continue
            if nid in graph.nodes:
                attached_node_ids.add(nid)
                continue
            if n.layer is not None and n.layer >= 1:
                n.layer = max_fo_layer + n.layer
            graph.nodes[nid] = n
            attached_node_ids.add(nid)

        for e in cs.subtree.edges:
            if e.src == cs_root_id:
                continue
            graph.edges.append(e)

        # Confidence per bridge = case-study similarity × per-link applicability.
        # A strong case-study match plus a strong per-link match produces a
        # high-confidence bridge; weakening either side pulls confidence down.
        # Bridges originate from the specific fo_node that claimed the analog
        # (and passed Layer B for this child). A shared case study can produce
        # multiple bridges per child — one per applicable claiming fo_node.
        unique_fo_bridges: set[str] = set()
        for cand, app in kept_acts:
            bridge_conf = round(cs.similarity_score * app.confidence, 4)
            indices_str = ",".join(app.relevant_indices) or "macro"
            graph.edges.append(
                Edge(
                    src=cand["fo_node_id"],
                    dst=cand["child_id"],
                    mechanism=f"historical analog: {cs.name} (applies-today via {indices_str})",
                    sensitivity=bridge_conf,
                    confidence=bridge_conf,
                )
            )
            unique_fo_bridges.add(cand["fo_node_id"])

        case_study_subtree_nodes[cs.id] = attached_node_ids
        n_attached += 1
        n_bridges_total = len(applies_by_cs.get(cs.id, []))
        shared_note = (
            f" [shared across {len(unique_fo_bridges)} first-order nodes]"
            if len(unique_fo_bridges) > 1 else ""
        )
        emit(
            "subtree_attached",
            f"  Attach {cs.name!r} via {len(kept_acts)}/{n_bridges_total} applicable bridges "
            f"(similarity {cs.similarity_score:.2f}){shared_note}",
            case_study_id=cs.id,
            name=cs.name,
            similarity=cs.similarity_score,
            first_order_ids=list(unique_fo_bridges),
            n_bridges=len(kept_acts),
            n_bridges_dropped=n_bridges_total - len(kept_acts),
            n_nodes_added=len(attached_node_ids),
        )

    pre_prune_graph = copy.deepcopy(graph)
    emit(
        "merged_graph_built",
        f"  Merged graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges ({n_attached} subtrees attached)",
        n_nodes=len(graph.nodes),
        n_edges=len(graph.edges),
        merged_graph=copy.deepcopy(pre_prune_graph),
    )

    # Stage 7.5: chain-level logic verification on the merged graph.
    # Walks root-to-leaf paths and checks cross-edge coherence (sign
    # composition, magnitude leaps, equivocation, time horizon, missing
    # transmission) — issues the per-edge Moderator can't see in isolation.
    # Edges flagged as the offending step in a failing chain are dropped.
    chain_verifications: dict[str, Any] = {}
    chain_drop_ids: set[str] = set()
    if graph.edges:
        emit("stage_chain_verify_start", "  Stage 7.5: chain-level logic verification")
        chain_verifications = logic_verifier.verify_paths(
            graph,
            model=model,
            client=client,
            run_id=run_id,
            max_paths=20,
            min_path_length=2,
            max_workers=4,
        )
        for chain_key, vresult in chain_verifications.items():
            if vresult.ok or vresult.failed_edge_idx is None:
                continue
            edge_ids_in_chain = chain_key.split("->")
            if 0 <= vresult.failed_edge_idx < len(edge_ids_in_chain):
                bad_id = edge_ids_in_chain[vresult.failed_edge_idx]
                chain_drop_ids.add(bad_id)
                emit(
                    "chain_failure",
                    f"    chain failed: {vresult.failure_category} at edge {vresult.failed_edge_idx} ({vresult.reason[:80]})",
                    chain=chain_key,
                    failed_edge_id=bad_id,
                    failure_category=vresult.failure_category,
                    reason=vresult.reason,
                )
        if chain_drop_ids:
            graph.edges = [e for e in graph.edges if e.id not in chain_drop_ids]
        emit(
            "stage_chain_verify_complete",
            f"  Chain verify: {len(chain_verifications)} chains, {len(chain_drop_ids)} edges dropped",
            n_chains=len(chain_verifications),
            n_dropped=len(chain_drop_ids),
        )

    similarity_by_cs = {cs.id: cs.similarity_score for cs in case_studies}
    # Pruner now uses subtree_nodes (set per case study) since cs_root has been
    # dropped during merge — there's no single subtree root to walk from.

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
        case_study_subtree_nodes=case_study_subtree_nodes,
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

    # Reconcile per-case-study subtree views with the post-prune merged graph.
    # state.subtrees in the UI is populated during stage 3 build; nothing
    # downstream updates it, so the multi-grid would otherwise show pre-prune
    # subtrees (with edges that have since been dropped by applies-today,
    # chain verify, or the pruner). For each cs, either:
    #   - emit subtree_dropped_finalize: nothing of this cs survived → UI removes the cell
    #   - emit subtree_finalized: rebuild a per-cs subview from the post-prune graph
    #     (cs root + surviving descendants + bridges visualized as cs_root → child)
    post_prune_node_ids = set(graph.nodes.keys())
    for cs in case_studies:
        cs_root_id = cs.subtree.root or _first_node_id(cs.subtree)
        surviving_descendants = case_study_subtree_nodes.get(cs.id, set()) & post_prune_node_ids
        if not surviving_descendants:
            emit(
                "subtree_dropped_finalize",
                f"  Drop subtree {cs.name!r} from grid: nothing survived post-prune",
                case_study_id=cs.id,
                name=cs.name,
            )
            continue
        view_nodes: dict[str, Node] = {}
        if cs_root_id and cs_root_id in cs.subtree.nodes:
            view_nodes[cs_root_id] = cs.subtree.nodes[cs_root_id]
        for nid in surviving_descendants:
            view_nodes[nid] = graph.nodes[nid]
        view_edges: list[Edge] = []
        for e in graph.edges:
            if e.src in surviving_descendants and e.dst in surviving_descendants:
                view_edges.append(e)
            elif (
                cs_root_id
                and e.dst in surviving_descendants
                and e.src not in surviving_descendants
                and "historical analog" in (e.mechanism or "")
            ):
                view_edges.append(Edge(
                    id=e.id,
                    src=cs_root_id,
                    dst=e.dst,
                    mechanism=e.mechanism,
                    sensitivity=e.sensitivity,
                    confidence=e.confidence,
                    supporting_data=list(e.supporting_data),
                ))
        finalized_graph = CausalGraph(
            nodes=view_nodes, edges=view_edges, root=cs_root_id or None,
        )
        emit(
            "subtree_finalized",
            f"  Finalize {cs.name!r}: {len(view_nodes)} nodes, {len(view_edges)} edges",
            case_study_id=cs.id,
            name=cs.name,
            subtree=copy.deepcopy(finalized_graph),
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

    # ------------------------------------------------------------------
    # Stage 9.5: Validate cited evidence against real FRED / Yahoo data.
    # Each cited_evidence string from adversary/defender/moderator
    # transcripts is parsed and checked against the live tools. Results
    # are cached on disk so repeated runs are fast. Statuses surface in
    # the UI per-citation as ✓ / ✗ / • badges.
    # ------------------------------------------------------------------
    emit("stage_start", "Stage 9.5: validate cited evidence vs FRED/Yahoo", stage=9)
    all_citations: list[str] = []
    for d in all_debates.values():
        if d.critique and d.critique.cited_evidence:
            all_citations.extend(d.critique.cited_evidence)
        if d.rebuttal and d.rebuttal.cited_evidence:
            all_citations.extend(d.rebuttal.cited_evidence)
    citation_validations = validate_citations(all_citations, tools) if all_citations else {}
    n_ok = sum(1 for v in citation_validations.values() if v == "ok")
    n_missing = sum(1 for v in citation_validations.values() if v == "missing")
    n_unver = sum(1 for v in citation_validations.values() if v == "unverifiable")
    emit(
        "stage_complete",
        f"Stage 9.5 done: {len(citation_validations)} unique citations "
        f"({n_ok} verified, {n_missing} missing, {n_unver} unverifiable)",
        n_ok=n_ok,
        n_missing=n_missing,
        n_unverifiable=n_unver,
    )

    return PipelineResult(
        graph=graph,
        pre_prune_graph=pre_prune_graph,
        case_studies=case_studies,
        portfolio_impacts=portfolio_impacts,
        debates=all_debates,
        comparator_results=comparator_results,
        case_study_to_first_order=case_study_to_first_order,
        case_study_to_first_order_ids=case_study_to_first_order_ids,
        tail_scenarios=tail_scenarios,
        chain_verifications=chain_verifications,
        citation_validations=citation_validations,
        link_applicabilities=link_applicabilities,
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


def _normalize_event_name(name: Optional[str]) -> Optional[str]:
    """Lowercase, strip punctuation, collapse whitespace. Returns None for
    empty / 'unknown' so cross-FO dedup falls back to a date+series key."""
    if not name:
        return None
    s = name.lower().strip()
    if s in ("unknown", ""):
        return None
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _episode_dedup_key(ep: Episode) -> str:
    """Heuristic identity key for cross-first-order analog dedup. Used as the
    fallback path when the LLM-based dedup fails. Two episodes that share a
    normalized candidate_event collapse to the same key. Episodes with no
    candidate_event fall back to `(year, month, series_id)` so two different
    FRED proxies with extrema in the same window aren't merged."""
    norm = _normalize_event_name(ep.candidate_event)
    if norm:
        return f"event:{norm}"
    return f"date:{ep.start.year:04d}-{ep.start.month:02d}:{ep.series_id}"


def _llm_dedup_episodes(
    episodes: list[Episode],
    *,
    model: str,
    client: Any,
    run_id: Optional[str] = None,
) -> list[str]:
    """One batch LLM call that groups historical episodes into equivalence
    classes. Catches semantic duplicates the string heuristic misses
    ("Russia invades Ukraine" ≡ "2022 Russia-Ukraine war"). Returns a list of
    group_id strings parallel to input order. On any failure, falls back to
    the per-episode heuristic key.

    One call per pipeline run regardless of how many episodes (~$0.01)."""
    if not episodes:
        return []
    if len(episodes) == 1:
        return [_episode_dedup_key(episodes[0])]

    items = [
        {
            "idx": i,
            "candidate_event": ep.candidate_event or "",
            "start_date": str(ep.start),
            "end_date": str(ep.end),
            "series_id": ep.series_id,
            "magnitude": round(ep.magnitude or 0.0, 2),
        }
        for i, ep in enumerate(episodes)
    ]
    user = (
        "Group these historical episodes into equivalence classes. Two episodes "
        "are equivalent if they refer to the same underlying historical event, "
        "even if labelled differently. Examples of equivalences:\n"
        " - 'Russia invades Ukraine' ≡ '2022 Russia-Ukraine war' ≡ "
        "'February 2022 Russian invasion'.\n"
        " - 'Lehman Brothers collapse' ≡ 'September 2008 financial crisis' ≡ "
        "'2008 GFC peak'.\n"
        " - 'COVID lockdown shock' ≡ 'March 2020 pandemic crash' ≡ "
        "'COVID-19 outbreak'.\n"
        "Be conservative: don't merge events that are merely topically related "
        "(e.g. two distinct rate-cut episodes are NOT the same event).\n\n"
        f"Episodes:\n{_json.dumps(items, indent=2, default=str)}\n\n"
        "Return JSON only with a 'groups' array, one entry per input idx in "
        "the same order. Use a short snake_case canonical id per group:\n"
        '{"groups": ["ukraine_invasion_2022", "ukraine_invasion_2022", '
        '"lehman_2008", ...]}'
    )
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning("LLM dedup call failed (%s); falling back to heuristic", exc)
        return [_episode_dedup_key(ep) for ep in episodes]

    text = msg.content[0].text if msg.content else ""
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        logger.warning("LLM dedup parse failed; falling back to heuristic")
        return [_episode_dedup_key(ep) for ep in episodes]
    groups = parsed.get("groups", [])
    if not isinstance(groups, list) or len(groups) != len(episodes):
        logger.warning(
            "LLM dedup returned %d groups for %d episodes; falling back to heuristic",
            len(groups) if isinstance(groups, list) else -1,
            len(episodes),
        )
        return [_episode_dedup_key(ep) for ep in episodes]
    return [str(g) if g else _episode_dedup_key(episodes[i]) for i, g in enumerate(groups)]


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
