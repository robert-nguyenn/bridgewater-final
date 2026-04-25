from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

import networkx as nx

from src.agents import sensitivity as sensitivity_agent
from src.config import LOGS_DIR, MODEL, MODEL_FAST, PROMPTS_DIR
from src.types import (
    CaseStudy,
    CausalGraph,
    Edge,
    Node,
    ToolBundle,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = PROMPTS_DIR / "tree_builder.md"

# Stop conditions. See CLAUDE.md "Stop conditions for the layer".
LAYER_CONFIDENCE_FLOOR = 0.5
DEFAULT_MAX_LAYERS = 3
DEFAULT_MAX_NODES = 40
MAX_CANDIDATES_PER_PARENT = 5
DEFAULT_SCORE_WORKERS = 4  # parallel candidate scoring within a layer

VALID_ASSET_CLASSES = {"equities", "futures", "commodities", "fx", "rates", "macro"}


@dataclass
class _Candidate:
    """Draft node + the proposed mechanism for the edge that would carry it.

    `existing_id`, when non-None, signals that this candidate is the same
    observable variable as an already-present node in the subtree. The build
    loop will add a new edge from the current parent to that existing node
    (multi-parent DAG) instead of creating a duplicate.
    """

    label: str
    description: str
    asset_class: Optional[str]
    mechanism: str
    existing_id: Optional[str] = None


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _call_model(prompt: str, *, model: str, system: str = "") -> str:
    """Call the Anthropic model. Tests monkeypatch this attribute."""
    from anthropic import Anthropic

    from src.config import ANTHROPIC_API_KEY

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text  # type: ignore[attr-defined]


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.startswith("```"))
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        s = raw.find(open_ch)
        e = raw.rfind(close_ch)
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(raw[s : e + 1])
            except json.JSONDecodeError:
                continue
    return {}


def _build_movers_summary(case_study: CaseStudy, tools: ToolBundle) -> str:
    """Return a short bulleted list of FRED series that moved during the case
    study window, ranked by abs(peak_z). Empty string on any failure.

    Fed into the propose prompt so new nodes are grounded in observed movers,
    not LLM priors. Cached at the FRED tool layer."""
    if tools is None or tools.fred is None or not hasattr(tools.fred, "window_movers"):
        return ""
    try:
        result = tools.fred.window_movers(case_study.date_range[0], case_study.date_range[1])
    except Exception as exc:
        logger.warning("window_movers failed: %s", exc)
        return ""
    if not isinstance(result, list) or not result:
        return ""
    lines = []
    for m in result[:8]:
        sid = m.get("series_id", "?")
        z = m.get("peak_z")
        direction = m.get("direction", "")
        if z is None:
            continue
        lines.append(f"  - {sid}: peak z={z:+.2f} ({direction})")
    return "\n".join(lines)


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return s[:40] or "node"


def _new_id(label: str) -> str:
    return f"{_slug(label)}_{uuid.uuid4().hex[:6]}"


def _log_call(run_id: Optional[str], stage: str, payload: dict[str, Any]) -> None:
    # TODO(integration): lift to a shared logger in src/logging.py
    if not run_id:
        return
    log_dir = LOGS_DIR / run_id / "tree_builder"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"stage": stage, **payload}, default=str) + "\n")


def _propose_children(
    parent: Node,
    case_study: CaseStudy,
    siblings: list[Node],
    *,
    all_existing: dict[str, Node],
    model: str,
    movers_summary: str = "",
) -> list[_Candidate]:
    sib_summary = ", ".join(s.label for s in siblings) if siblings else "(none)"
    # Show every node already in the subtree so the model can merge instead of
    # creating duplicates. Cap the listing to keep the prompt bounded.
    existing_lines = [
        f"  - {nid}: {n.label}"
        for nid, n in list(all_existing.items())[:60]
        if nid != parent.id
    ]
    existing_summary = "\n".join(existing_lines) if existing_lines else "  (none)"
    movers_block = (
        f"Variables that empirically moved during the case-study window (FRED, peak z-scores):\n{movers_summary}\n"
        "Prefer proposing nodes for variables in this list when they fit the parent's mechanism. "
        "These are observed responders, not priors.\n\n"
        if movers_summary else ""
    )

    user = (
        "PROPOSE_CHILDREN.\n"
        f"Case study: {case_study.name} "
        f"({case_study.date_range[0]} to {case_study.date_range[1]}).\n"
        f"Triggering event: {case_study.triggering_event}.\n"
        f"Parent node (layer {parent.layer}): {parent.label}. {parent.description}\n"
        f"Existing siblings under this parent: {sib_summary}.\n"
        f"All existing nodes in the subtree (id: label):\n{existing_summary}\n\n"
        f"{movers_block}"
        "Propose 3 to 5 candidate downstream nodes that could plausibly follow from the parent "
        "within the case study window. Cover diverse asset classes and transmission channels.\n"
        "**For each candidate, decide MERGE or NEW.** If the candidate is the same observable variable "
        "as an existing node above (e.g. 'S&P 500 drawdown' merges with 'S&P 500 selloff'), set "
        "`existing_id` to that node's id and only provide `mechanism`. This produces a multi-parent "
        "DAG where popular variables acquire multiple incoming edges. Otherwise set `existing_id: null` "
        "and provide all fields. Avoid restatements of the parent.\n"
        "Respond with JSON only, a list of objects:\n"
        '[{"label": "short label", "description": "one or two sentences", '
        '"asset_class": "equities|futures|commodities|fx|rates|macro", '
        '"mechanism": "one sentence parent->child mechanism", "existing_id": null}, '
        '{"existing_id": "node_xyz", "mechanism": "parent->existing mechanism"}]'
    )
    raw = _call_model(user, model=model, system=_load_prompt())
    parsed = _parse_json(raw)
    if isinstance(parsed, dict):
        parsed = parsed.get("candidates") or parsed.get("nodes") or []
    if not isinstance(parsed, list):
        return []

    out: list[_Candidate] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        existing_id = item.get("existing_id")
        if isinstance(existing_id, str) and existing_id and existing_id in all_existing:
            # Merge candidate: borrow label/description/asset_class from the existing node.
            existing_node = all_existing[existing_id]
            mech = str(item.get("mechanism") or f"{parent.label} drives {existing_node.label}").strip()
            out.append(
                _Candidate(
                    label=existing_node.label,
                    description=existing_node.description,
                    asset_class=existing_node.asset_class,
                    mechanism=mech,
                    existing_id=existing_id,
                )
            )
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        asset_class = item.get("asset_class")
        if asset_class is not None and asset_class not in VALID_ASSET_CLASSES:
            asset_class = None
        out.append(
            _Candidate(
                label=label,
                description=str(item.get("description") or label).strip(),
                asset_class=asset_class,
                mechanism=str(item.get("mechanism") or f"{parent.label} drives {label}").strip(),
            )
        )
    return out[:MAX_CANDIDATES_PER_PARENT]


def _challenge_candidate(
    parent: Node,
    candidate: _Candidate,
    siblings: list[Node],
    *,
    model: str,
) -> dict[str, Any]:
    sib_lines = (
        "\n".join(f"- {s.label}: {s.description}" for s in siblings) if siblings else "(none)"
    )
    user = (
        "CHALLENGE_CANDIDATE.\n"
        f"Parent: {parent.label}. {parent.description}\n"
        f"Candidate: {candidate.label}. {candidate.description}\n"
        f"Proposed mechanism: {candidate.mechanism}.\n"
        f"Existing siblings under the parent:\n{sib_lines}\n\n"
        "Decide one of: keep, drop, merge. Drop if the candidate is a restatement of the parent, "
        "the asset class is wrong, or the mechanism is a tautology. Merge if it is redundant "
        "with an existing sibling. Otherwise keep.\n"
        'Respond with JSON only: {"action": "keep|drop|merge", '
        '"merge_with": "<sibling label or null>", "reason": "one sentence"}'
    )
    raw = _call_model(user, model=model, system=_load_prompt())
    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        return {"action": "keep", "reason": "challenger returned malformed response"}
    return parsed


def _to_networkx(graph: CausalGraph) -> nx.DiGraph:
    g = nx.DiGraph()
    for nid in graph.nodes:
        g.add_node(nid)
    for e in graph.edges:
        g.add_edge(e.src, e.dst)
    return g


def _add_edge_if_dag(graph: CausalGraph, edge: Edge) -> bool:
    """Add edge if the resulting graph stays acyclic. Return True on success."""
    g = _to_networkx(graph)
    g.add_edge(edge.src, edge.dst)
    if not nx.is_directed_acyclic_graph(g):
        return False
    graph.edges.append(edge)
    return True


def _leaves(graph: CausalGraph) -> list[str]:
    has_outgoing = {e.src for e in graph.edges}
    return [
        nid for nid in graph.nodes if nid not in has_outgoing and nid != graph.root
    ]


def _drop_node(graph: CausalGraph, node_id: str) -> None:
    graph.nodes.pop(node_id, None)
    graph.edges = [e for e in graph.edges if e.src != node_id and e.dst != node_id]


def _layer_max_confidence(graph: CausalGraph, layer_node_ids: set[str]) -> float:
    return max(
        (e.confidence for e in graph.edges if e.dst in layer_node_ids),
        default=0.0,
    )


def build_subtree(
    case_study: CaseStudy,
    *,
    tools: ToolBundle,
    model: str = MODEL,
    model_fast: str = MODEL_FAST,
    max_layers: int = DEFAULT_MAX_LAYERS,
    max_nodes: int = DEFAULT_MAX_NODES,
    run_id: Optional[str] = None,
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
    on_layer_complete: Optional[
        Callable[[CausalGraph, list[Edge], int], set[str]]
    ] = None,
) -> CaseStudy:
    """Expand a CaseStudy's triggering event into a 2 to 3 layer DAG.

    Does not mutate `case_study`. Returns a new CaseStudy with `subtree`
    populated.

    `on_progress`, when provided, receives event dicts as the subtree grows:
    `subtree_init`, `subtree_layer_start`, `subtree_candidate_added`,
    `subtree_candidate_merged`, `subtree_layer_complete` (with a
    `partial_graph` snapshot). The orchestrator forwards these into its
    ProgressEvent stream so the UI can render the subtree being built.

    `on_layer_complete`, when provided, is called after every layer with
    `(graph, new_edges_this_layer, layer)` and must return the set of
    `edge.id` values to drop. The orchestrator uses this to run an
    adversarial debate (Adversary → Defender → Moderator) on each layer's
    new edges; losers are pruned before the next layer expands so weak
    edges don't propagate."""
    import copy as _copy

    graph = CausalGraph()

    root_id = _new_id(case_study.triggering_event)
    root_label = case_study.triggering_event[:80]
    root = Node(
        id=root_id,
        label=root_label,
        description=case_study.triggering_event,
        layer=0,
    )
    graph.nodes[root_id] = root
    graph.root = root_id

    def _emit(kind: str, **data: Any) -> None:
        if on_progress is not None:
            on_progress({"kind": kind, **data})

    _emit(
        "subtree_init",
        case_study_id=case_study.id,
        name=case_study.name,
        root_id=root_id,
        root_label=root_label,
        partial_graph=_copy.deepcopy(graph),
    )

    # Empirical movers summary: which FRED reference series moved during the
    # case study window. Computed once per subtree, fed into every propose
    # prompt so the LLM grounds new nodes in observed movers, not priors.
    movers_summary = _build_movers_summary(case_study, tools)

    layer_nodes: dict[int, list[Node]] = {0: [root]}

    for layer in range(1, max_layers + 1):
        prev_layer = layer_nodes.get(layer - 1, [])
        if not prev_layer:
            break
        new_layer: list[Node] = []
        edges_added_this_layer: list[Edge] = []
        _emit("subtree_layer_start", layer=layer, case_study_id=case_study.id)

        for parent in prev_layer:
            if len(graph.nodes) >= max_nodes:
                break
            siblings: list[Node] = []
            candidates = _propose_children(
                parent, case_study, siblings,
                all_existing=graph.nodes, model=model_fast,
                movers_summary=movers_summary,
            )

            # ----------------------------------------------------------
            # Phase 1: parallel resolution + score_edge for each candidate.
            # SensitivityAgent's data fetch + LLM scoring is the expensive
            # step; running them concurrently is the biggest within-layer
            # win. Commits stay sequential below to keep DAG state consistent.
            # ----------------------------------------------------------
            def _resolve_and_score(cand: _Candidate, _parent: Node = parent):
                if cand.existing_id and cand.existing_id in graph.nodes:
                    existing_node = graph.nodes[cand.existing_id]
                    if existing_node.id == _parent.id:
                        return None
                    if any(
                        e.src == _parent.id and e.dst == existing_node.id
                        for e in graph.edges
                    ):
                        return None
                    score = sensitivity_agent.score_edge(
                        parent=_parent,
                        candidate=existing_node,
                        mechanism=cand.mechanism,
                        case_study=case_study,
                        tools=tools,
                        model=model,
                        run_id=run_id,
                    )
                    return ("merge", cand, existing_node, score)
                cand_id = _new_id(cand.label)
                cand_node = Node(
                    id=cand_id,
                    label=cand.label,
                    description=cand.description,
                    layer=layer,
                    asset_class=cand.asset_class,
                )
                score = sensitivity_agent.score_edge(
                    parent=_parent,
                    candidate=cand_node,
                    mechanism=cand.mechanism,
                    case_study=case_study,
                    tools=tools,
                    model=model,
                    run_id=run_id,
                )
                return ("new", cand, cand_node, score)

            with ThreadPoolExecutor(max_workers=DEFAULT_SCORE_WORKERS) as executor:
                futures = [executor.submit(_resolve_and_score, c) for c in candidates]
                resolved = [f.result() for f in futures]

            # ----------------------------------------------------------
            # Phase 2: sequential commit. DAG check, _challenge_candidate (NEW
            # path only, sees current siblings), and graph mutations happen
            # one candidate at a time so state is consistent.
            # ----------------------------------------------------------
            for entry in resolved:
                if entry is None:
                    continue
                if len(graph.nodes) >= max_nodes:
                    break
                mode, cand, target, score = entry

                if not score.keep:
                    _log_call(
                        run_id,
                        "drop_after_score",
                        {"parent": parent.id, "candidate": cand.label, "reason": score.keep_reason},
                    )
                    continue

                # MERGE path: edge from parent to an existing graph node.
                if mode == "merge":
                    existing_node = target
                    if existing_node.id == parent.id:
                        continue  # self-loop
                    if any(
                        e.src == parent.id and e.dst == existing_node.id
                        for e in graph.edges
                    ):
                        continue  # duplicate edge added by an earlier iteration

                    merge_edge = Edge(
                        src=parent.id,
                        dst=existing_node.id,
                        mechanism=score.mechanism_refined,
                        sensitivity=score.sensitivity,
                        confidence=score.confidence,
                        supporting_data=list(score.supporting_data),
                    )
                    if not _add_edge_if_dag(graph, merge_edge):
                        _log_call(
                            run_id,
                            "drop_merge_cycle",
                            {"parent": parent.id, "existing": existing_node.id},
                        )
                        continue

                    edges_added_this_layer.append(merge_edge)
                    _emit(
                        "subtree_candidate_merged",
                        case_study_id=case_study.id,
                        layer=layer,
                        parent_id=parent.id,
                        parent_label=parent.label,
                        existing_id=existing_node.id,
                        existing_label=existing_node.label,
                        mechanism=score.mechanism_refined,
                        sensitivity=score.sensitivity,
                        confidence=score.confidence,
                        partial_graph=_copy.deepcopy(graph),
                    )
                    continue

                # NEW path: target is a freshly built Node, score is in hand.
                cand_node = target
                cand_node.magnitude_estimate = score.magnitude_estimate
                cand_node.evidence = list(score.supporting_data)

                # Challenger sees current siblings; runs sequentially so it
                # has visibility into already-committed candidates this layer.
                challenge = _challenge_candidate(parent, cand, siblings, model=model_fast)
                action = str(challenge.get("action") or "keep").lower()

                if action == "drop":
                    _log_call(
                        run_id,
                        "drop_after_challenge",
                        {"parent": parent.id, "candidate": cand.label, "reason": challenge.get("reason")},
                    )
                    continue

                if action == "merge":
                    target_label = challenge.get("merge_with")
                    target_sib = next(
                        (s for s in siblings if s.label == target_label),
                        None,
                    )
                    if target_sib is not None:
                        target_edge = next(
                            (e for e in graph.edges if e.dst == target_sib.id),
                            None,
                        )
                        if target_edge is not None and score.confidence > target_edge.confidence:
                            cand_node.evidence.extend(target_sib.evidence)
                            _drop_node(graph, target_sib.id)
                            siblings.remove(target_sib)
                            new_layer = [n for n in new_layer if n.id != target_sib.id]
                            # Fall through to commit the new candidate.
                        else:
                            target_sib.evidence.extend(cand_node.evidence)
                            _log_call(
                                run_id,
                                "merge_into_sibling",
                                {
                                    "parent": parent.id,
                                    "kept": target_sib.label,
                                    "merged": cand.label,
                                },
                            )
                            continue

                edge = Edge(
                    src=parent.id,
                    dst=cand_node.id,
                    mechanism=score.mechanism_refined,
                    sensitivity=score.sensitivity,
                    confidence=score.confidence,
                    supporting_data=list(score.supporting_data),
                )
                graph.nodes[cand_node.id] = cand_node
                if not _add_edge_if_dag(graph, edge):
                    graph.nodes.pop(cand_node.id, None)
                    _log_call(
                        run_id,
                        "drop_cycle",
                        {"parent": parent.id, "candidate": cand.label},
                    )
                    continue

                siblings.append(cand_node)
                new_layer.append(cand_node)
                edges_added_this_layer.append(edge)
                _emit(
                    "subtree_candidate_added",
                    case_study_id=case_study.id,
                    layer=layer,
                    parent_id=parent.id,
                    parent_label=parent.label,
                    candidate_id=cand_node.id,
                    candidate_label=cand.label,
                    mechanism=score.mechanism_refined,
                    sensitivity=score.sensitivity,
                    confidence=score.confidence,
                    partial_graph=_copy.deepcopy(graph),
                )

        # Per-layer adversarial debate: drop edges that lose before expanding.
        if on_layer_complete is not None and edges_added_this_layer:
            try:
                drop_ids = on_layer_complete(graph, edges_added_this_layer, layer)
            except Exception as exc:
                logger.warning("on_layer_complete raised: %s", exc)
                drop_ids = set()
            if drop_ids:
                kept_edges = []
                dropped_edges: list[Edge] = []
                for e in graph.edges:
                    if e.id in drop_ids:
                        dropped_edges.append(e)
                    else:
                        kept_edges.append(e)
                graph.edges = kept_edges
                # Drop nodes that lost their only inbound edge (orphans).
                for de in dropped_edges:
                    if de.dst == graph.root:
                        continue
                    if not any(e.dst == de.dst for e in graph.edges):
                        graph.nodes.pop(de.dst, None)
                        new_layer = [n for n in new_layer if n.id != de.dst]
                _emit(
                    "subtree_layer_pruned",
                    case_study_id=case_study.id,
                    layer=layer,
                    n_dropped=len(dropped_edges),
                    n_kept=len(edges_added_this_layer) - len(dropped_edges),
                    partial_graph=_copy.deepcopy(graph),
                )

        if not new_layer:
            break
        layer_nodes[layer] = new_layer

        layer_ids = {n.id for n in new_layer}
        if _layer_max_confidence(graph, layer_ids) < LAYER_CONFIDENCE_FLOOR:
            break

    _enforce_leaf_asset_class(graph)

    return CaseStudy(
        name=case_study.name,
        date_range=case_study.date_range,
        triggering_event=case_study.triggering_event,
        macro_snapshot=case_study.macro_snapshot,
        similarity_score=case_study.similarity_score,
        subtree=graph,
    )


def _enforce_leaf_asset_class(graph: CausalGraph) -> None:
    """Drop leaves with no asset_class. PortfolioAgent requires it."""
    # Iterate to a fixed point so newly-orphaned interior nodes also get checked.
    while True:
        offenders = [
            nid
            for nid in _leaves(graph)
            if graph.nodes[nid].asset_class is None
        ]
        if not offenders:
            return
        for nid in offenders:
            _drop_node(graph, nid)


def run(
    seed: Node,
    *,
    tools: ToolBundle,
    model: str = MODEL,
    depth: int = 3,
) -> CausalGraph:
    """Compatibility shim. Prefer build_subtree(case_study, ...) which carries
    the case study window required for sensitivity scoring."""
    raise NotImplementedError(
        "Use build_subtree(case_study, ...). A bare seed Node lacks the date "
        "range that sensitivity scoring requires."
    )
