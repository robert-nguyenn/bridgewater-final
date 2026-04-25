from __future__ import annotations

from typing import Any, Callable, Optional

from src.types import CausalGraph, Edge, ToolBundle

PrunerEventCallback = Callable[[dict[str, Any]], None]


def run(
    graph: CausalGraph,
    *,
    debates: Optional[dict[str, Any]] = None,
    comparator: Optional[dict[str, float]] = None,
    case_study_subtree_roots: Optional[dict[str, str]] = None,
    case_study_subtree_nodes: Optional[dict[str, set[str]]] = None,
    debate_margin_threshold: float = 0.0,
    similarity_threshold: float = 0.3,
    tools: Optional[ToolBundle] = None,
    model: str = "",
    client: Any = None,
    run_id: Optional[str] = None,
    on_event: Optional[PrunerEventCallback] = None,
) -> CausalGraph:
    """Prune edges that lost their adversarial debate and case-study subtrees
    whose macro regime is too far from today.

    Three steps:
    1. Drop edges where defender_score - critique_score < `debate_margin_threshold`.
    2. Drop subtrees whose comparator similarity is below `similarity_threshold`
       (cuts the subtree root and everything reachable only through it).
    3. Garbage-collect nodes no longer reachable from the graph root.

    `on_event`, if provided, receives a dict per pruning decision so a UI can
    stream the process. Event kinds: `edge_pruned`, `subtree_dropped`,
    `node_orphaned`, `pruning_summary`.
    """
    debates = debates or {}
    comparator = comparator or {}
    case_study_subtree_roots = case_study_subtree_roots or {}
    case_study_subtree_nodes = case_study_subtree_nodes or {}

    def emit(kind: str, **data: Any) -> None:
        if on_event is not None:
            on_event({"kind": kind, **data})

    # 1. Edge debate filter. Moderator verdict wins when present; otherwise
    # fall back to the score-margin rule. Bridge edges (historical analogs)
    # are exempt from debate-based pruning since they're meta-references,
    # not causal claims; their fate is governed by case-study similarity.
    surviving_edges: list[Edge] = []
    for edge in graph.edges:
        if edge.mechanism and edge.mechanism.startswith("historical analog:"):
            surviving_edges.append(edge)
            continue
        debate = debates.get(edge.id)
        if debate is not None:
            verdict = getattr(debate, "verdict", None)
            margin = debate.rebuttal.score - debate.critique.score
            should_drop = False
            reason = ""
            if verdict is not None:
                if verdict.decision == "drop":
                    should_drop = True
                    reason = "moderator_dropped"
            else:
                if margin < debate_margin_threshold:
                    should_drop = True
                    reason = "lost_debate"
            if should_drop:
                src_label = graph.nodes[edge.src].label if edge.src in graph.nodes else edge.src
                dst_label = graph.nodes[edge.dst].label if edge.dst in graph.nodes else edge.dst
                emit(
                    "edge_pruned",
                    edge_id=edge.id,
                    src=edge.src,
                    dst=edge.dst,
                    src_label=src_label,
                    dst_label=dst_label,
                    mechanism=edge.mechanism,
                    reason=reason,
                    adversary_score=debate.critique.score,
                    defender_score=debate.rebuttal.score,
                    margin=margin,
                    moderator_reasoning=getattr(verdict, "reasoning", None) if verdict else None,
                )
                continue
        surviving_edges.append(edge)

    # 2. Subtree similarity filter. Prefer the explicit `subtree_nodes` set
    # (used after the cs_root removal in stage 7); fall back to legacy
    # reachability-from-root when only `subtree_roots` is provided.
    excluded: set[str] = set()
    for cs_key, similarity in comparator.items():
        if similarity >= similarity_threshold:
            continue
        explicit_nodes = case_study_subtree_nodes.get(cs_key)
        if explicit_nodes:
            subtree_nodes = set(explicit_nodes)
            subtree_root = "(multi-root)"
        else:
            subtree_root = case_study_subtree_roots.get(cs_key)
            if not subtree_root:
                continue
            subtree_nodes = _reachable_from(subtree_root, surviving_edges)
        excluded |= subtree_nodes
        emit(
            "subtree_dropped",
            case_study=cs_key,
            subtree_root=subtree_root,
            similarity=similarity,
            threshold=similarity_threshold,
            n_nodes=len(subtree_nodes),
        )

    # 3. Reachability GC.
    if graph.root and graph.root in graph.nodes:
        reachable = _reachable_from(
            graph.root, surviving_edges, excluded=excluded
        )
    else:
        reachable = set(graph.nodes) - excluded

    pruned_nodes = {nid: n for nid, n in graph.nodes.items() if nid in reachable}
    pruned_edges = [e for e in surviving_edges if e.src in reachable and e.dst in reachable]

    orphans = set(graph.nodes) - reachable - excluded
    for orphan_id in orphans:
        emit(
            "node_orphaned",
            node_id=orphan_id,
            label=graph.nodes[orphan_id].label,
            reason="no path from root after edge filter",
        )

    emit(
        "pruning_summary",
        nodes_in=len(graph.nodes),
        nodes_out=len(pruned_nodes),
        edges_in=len(graph.edges),
        edges_out=len(pruned_edges),
        edges_dropped=len(graph.edges) - len(pruned_edges),
        nodes_dropped=len(graph.nodes) - len(pruned_nodes),
    )

    return CausalGraph(nodes=pruned_nodes, edges=pruned_edges, root=graph.root)


def _reachable_from(
    start: str, edges: list[Edge], *, excluded: Optional[set[str]] = None
) -> set[str]:
    excluded = excluded or set()
    if start in excluded:
        return set()
    reachable = {start}
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e.src in reachable and e.dst not in reachable and e.dst not in excluded:
                reachable.add(e.dst)
                changed = True
    return reachable
