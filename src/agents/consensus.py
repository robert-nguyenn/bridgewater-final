"""ConsensusAgent — cluster nodes across surviving case-study subtrees into
archetypes, then pool edges into a single consensus DAG.

Stage 11 of the pipeline. The merged + pruned graph already has bridges and
debate-pruned edges removed; this agent's job is to surface convergent
reasoning: when three independent historical analogs all say "shock → USD
strengthens → EM equities decline", that recurrence is the signal worth
showing in the headline view, not the per-analog rewordings.

The output ConsensusGraph keeps full attribution (which case studies
contributed which original nodes/edges) so the UI can show the "appears in:
Fukushima 2011, Iraq 2003, COVID 2020" attribution list per cluster."""
from __future__ import annotations

import json as _json
import logging
from typing import Any, Optional

from src.agents._common import extract_json
from src.config import ANTHROPIC_API_KEY, MODEL_FAST, PROMPTS_DIR
from src.types import (
    CaseStudy,
    CausalGraph,
    ConsensusEdge,
    ConsensusGraph,
    ConsensusNode,
    Node,
    _new_consensus_node_id,
)

logger = logging.getLogger(__name__)


def _llm_cluster_nodes(
    nodes: list[Node],
    *,
    model: str,
    client: Any,
) -> list[str]:
    """One batch LLM call. Returns a list of cluster_id strings parallel to
    `nodes`. On any failure, falls back to one cluster per node (id = node.id)
    so the rest of the pipeline degrades to "no clustering" rather than
    crashing."""
    if not nodes:
        return []
    if len(nodes) == 1:
        return [f"single_{nodes[0].id}"]

    items = [
        {
            "idx": i,
            "label": n.label,
            "description": (n.description or "")[:300],
            "asset_class": n.asset_class or "",
            "layer": n.layer,
        }
        for i, n in enumerate(nodes)
    ]
    system = (PROMPTS_DIR / "consensus.md").read_text()
    user = (
        f"Cluster these {len(nodes)} causal-graph nodes into archetypes. "
        f"Return JSON only with a 'clusters' array of length {len(nodes)} "
        f"in the same order.\n\nNodes:\n{_json.dumps(items, indent=2)}"
    )
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning("consensus clustering call failed (%s); singletons", exc)
        return [f"single_{n.id}" for n in nodes]

    text = msg.content[0].text if msg.content else ""
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        logger.warning("consensus parse failed; singletons")
        return [f"single_{n.id}" for n in nodes]
    clusters = parsed.get("clusters", [])
    if not isinstance(clusters, list) or len(clusters) != len(nodes):
        logger.warning(
            "consensus returned %d clusters for %d nodes; singletons",
            len(clusters) if isinstance(clusters, list) else -1,
            len(nodes),
        )
        return [f"single_{n.id}" for n in nodes]
    # Sanitize: stringify and fall back to per-node singleton on empty entries.
    return [
        str(c) if c else f"single_{nodes[i].id}"
        for i, c in enumerate(clusters)
    ]


def _pick_canonical(nodes: list[Node]) -> Node:
    """Choose the cluster's display node. Heuristic: the node with the most
    descriptive label (longest, ties broken by longest description). Cheap
    and language-agnostic; if needed we can swap for an LLM-pick later."""
    return max(
        nodes,
        key=lambda n: (len(n.label or ""), len(n.description or "")),
    )


def _median_int(xs: list[int]) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return s[len(s) // 2]


def build_consensus(
    base_graph: CausalGraph,
    case_study_subtree_nodes: dict[str, set[str]],
    case_studies: list[CaseStudy],
    *,
    model: str = MODEL_FAST,
    client: Any = None,
    run_id: Optional[str] = None,
) -> ConsensusGraph:
    """Build a consensus archetype graph from the post-prune merged graph.

    The trunk (root + first-order nodes, plus anything not contributed by a
    case-study subtree) passes through as singleton clusters — IdeaAgent ran
    once and produced one root and one set of FO nodes, deduping them across
    themselves makes no semantic sense. Subtree-contributed nodes get
    LLM-clustered into archetypes; edges are remapped through the cluster
    assignment and pooled (same src_cluster, same dst_cluster → one
    consensus edge with full original-edge attribution preserved)."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ------------------------------------------------------------------
    # 1. Partition nodes: trunk vs subtree-contributed.
    # ------------------------------------------------------------------
    subtree_node_ids: set[str] = set()
    for nids in case_study_subtree_nodes.values():
        subtree_node_ids |= set(nids)
    # Restrict to nodes actually present in the post-prune graph; some
    # subtree-claimed nodes may have been dropped by the pruner.
    subtree_node_ids &= set(base_graph.nodes.keys())
    trunk_node_ids = set(base_graph.nodes.keys()) - subtree_node_ids

    subtree_nodes = [base_graph.nodes[nid] for nid in subtree_node_ids]

    # ------------------------------------------------------------------
    # 2. Cluster subtree-contributed nodes via one batched LLM call.
    # ------------------------------------------------------------------
    cluster_ids = _llm_cluster_nodes(subtree_nodes, model=model, client=client)
    # original_node_id -> cluster_id
    cluster_by_node: dict[str, str] = {
        n.id: cid for n, cid in zip(subtree_nodes, cluster_ids)
    }
    # Trunk nodes map to themselves; consensus node id == original id keeps
    # the trunk identifiers stable in the consensus graph.
    for nid in trunk_node_ids:
        cluster_by_node[nid] = nid

    # ------------------------------------------------------------------
    # 3. Build per-cluster member list.
    # ------------------------------------------------------------------
    members_by_cluster: dict[str, list[Node]] = {}
    for nid, cid in cluster_by_node.items():
        node = base_graph.nodes.get(nid)
        if node is not None:
            members_by_cluster.setdefault(cid, []).append(node)

    # original_node_id -> contributing case study id ("trunk" if not from a cs)
    cs_owner: dict[str, str] = {}
    for cs_id, nids in case_study_subtree_nodes.items():
        for nid in nids:
            # First claimer wins. Multi-parent already handled upstream.
            cs_owner.setdefault(nid, cs_id)
    for nid in trunk_node_ids:
        cs_owner.setdefault(nid, "trunk")

    n_total_cs = max(1, len(case_studies))
    consensus_nodes: dict[str, ConsensusNode] = {}
    cluster_to_id: dict[str, str] = {}  # logical cluster string -> ConsensusNode.id

    for cid, members in members_by_cluster.items():
        contributing_cs: set[str] = set()
        member_attrib: list[tuple[str, str]] = []
        for n in members:
            owner = cs_owner.get(n.id, "trunk")
            contributing_cs.add(owner)
            member_attrib.append((owner, n.id))
        canonical = _pick_canonical(members)
        # Trunk members keep their original id; clustered nodes get a fresh
        # consensus id so we don't accidentally collide with trunk ids.
        is_trunk_singleton = (
            len(members) == 1 and members[0].id in trunk_node_ids
        )
        cnode_id = members[0].id if is_trunk_singleton else _new_consensus_node_id()
        cluster_to_id[cid] = cnode_id
        consensus_nodes[cnode_id] = ConsensusNode(
            id=cnode_id,
            label=canonical.label,
            description=canonical.description,
            layer=_median_int([m.layer or 0 for m in members]),
            asset_class=canonical.asset_class,
            member_node_ids=member_attrib,
            member_count=len(contributing_cs),
            consensus_weight=round(
                len(contributing_cs - {"trunk"}) / n_total_cs, 4
            ) if contributing_cs - {"trunk"} else 1.0,
        )

    # ------------------------------------------------------------------
    # 4. Remap edges through cluster_by_node, pool same-(src,dst) groups.
    # ------------------------------------------------------------------
    def to_cnode(orig_id: str) -> Optional[str]:
        cid = cluster_by_node.get(orig_id)
        return cluster_to_id.get(cid) if cid is not None else None

    pooled: dict[tuple[str, str], list[tuple[str, str]]] = {}
    # (src_cnode, dst_cnode) -> list of (cs_owner, original_edge_id) attribution
    pooled_edges_full: dict[tuple[str, str], list[Any]] = {}
    for e in base_graph.edges:
        src_c = to_cnode(e.src)
        dst_c = to_cnode(e.dst)
        if src_c is None or dst_c is None:
            continue
        if src_c == dst_c:
            # Cluster collapsed both endpoints; intra-cluster elaboration is
            # already represented by the cluster's existence. Skip.
            continue
        key = (src_c, dst_c)
        # Owner attribution per edge: prefer dst's owner (it's the "effect"
        # the edge produced), fall back to src.
        owner = cs_owner.get(e.dst) or cs_owner.get(e.src) or "trunk"
        pooled.setdefault(key, []).append((owner, e.id))
        pooled_edges_full.setdefault(key, []).append(e)

    consensus_edges: list[ConsensusEdge] = []
    for key, attribs in pooled.items():
        edges = pooled_edges_full[key]
        contributing_cs = {owner for owner, _ in attribs} - {"trunk"}
        avg_s = sum(e.sensitivity for e in edges) / len(edges)
        avg_c = sum(e.confidence for e in edges) / len(edges)
        # Pick the canonical mechanism: longest one (richest description).
        canonical = max(edges, key=lambda e: len(e.mechanism or ""))
        member_mechanisms = [e.mechanism for e in edges if e.mechanism]
        consensus_edges.append(
            ConsensusEdge(
                src=key[0],
                dst=key[1],
                canonical_mechanism=canonical.mechanism or "",
                avg_sensitivity=round(avg_s, 4),
                avg_confidence=round(avg_c, 4),
                consensus_weight=round(
                    len(contributing_cs) / n_total_cs, 4
                ) if contributing_cs else 0.0,
                member_count=max(1, len(contributing_cs)),
                member_edge_ids=attribs,
                member_mechanisms=member_mechanisms,
            )
        )

    # ------------------------------------------------------------------
    # 5. Assemble the consensus graph. Root preserves the original event id
    # so the UI can render with the same root convention as other views.
    # ------------------------------------------------------------------
    cs_index = {cs.id: cs.name for cs in case_studies}
    cs_index["trunk"] = "trunk"

    consensus_root = base_graph.root if base_graph.root in consensus_nodes else None

    return ConsensusGraph(
        nodes=consensus_nodes,
        edges=consensus_edges,
        root=consensus_root,
        case_study_index=cs_index,
    )
