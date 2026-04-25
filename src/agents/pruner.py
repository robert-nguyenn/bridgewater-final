from __future__ import annotations

from typing import Any, Optional

from src.types import CausalGraph, Edge, ToolBundle


def run(
    graph: CausalGraph,
    *,
    debates: Optional[dict[str, Any]] = None,  # edge_id -> Debate
    comparator: Optional[dict[str, float]] = None,  # case_study_name -> similarity
    case_study_subtree_roots: Optional[dict[str, str]] = None,  # case_study_name -> subtree_root_id
    debate_margin_threshold: float = 0.0,
    similarity_threshold: float = 0.3,
    tools: Optional[ToolBundle] = None,
    model: str = "",
    client: Any = None,
    run_id: Optional[str] = None,
) -> CausalGraph:
    """Prune edges that lost their adversarial debate and case-study subtrees
    whose macro regime is too far from today.

    Three steps:
    1. Drop edges where defender_score - critique_score < `debate_margin_threshold`.
    2. Drop subtrees whose comparator similarity is below `similarity_threshold`
       (cuts the subtree root and everything reachable only through it).
    3. Garbage-collect nodes no longer reachable from the graph root.

    The graph root is always preserved. Pure structural pruning; no LLM call.
    `tools`, `model`, `client`, `run_id` kept for canonical agent shape.
    """
    debates = debates or {}
    comparator = comparator or {}
    case_study_subtree_roots = case_study_subtree_roots or {}

    # 1. Drop edges that lost the debate.
    surviving_edges: list[Edge] = []
    for edge in graph.edges:
        debate = debates.get(edge.id)
        if debate is not None:
            margin = debate.rebuttal.score - debate.critique.score
            if margin < debate_margin_threshold:
                continue
        surviving_edges.append(edge)

    # 2. Identify nodes inside case-study subtrees that fail the similarity gate.
    excluded: set[str] = set()
    for cs_name, similarity in comparator.items():
        if similarity >= similarity_threshold:
            continue
        subtree_root = case_study_subtree_roots.get(cs_name)
        if not subtree_root:
            continue
        excluded |= _reachable_from(subtree_root, surviving_edges)

    # 3. Garbage-collect nodes no longer reachable from the root, skipping excluded.
    if graph.root and graph.root in graph.nodes:
        reachable = _reachable_from(
            graph.root, surviving_edges, excluded=excluded
        )
    else:
        reachable = set(graph.nodes) - excluded

    pruned_nodes = {nid: n for nid, n in graph.nodes.items() if nid in reachable}
    pruned_edges = [e for e in surviving_edges if e.src in reachable and e.dst in reachable]

    return CausalGraph(nodes=pruned_nodes, edges=pruned_edges, root=graph.root)


def _reachable_from(
    start: str, edges: list[Edge], *, excluded: Optional[set[str]] = None
) -> set[str]:
    """All nodes reachable from `start` via `edges`, optionally skipping `excluded`."""
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
