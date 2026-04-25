from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import networkx as nx

from src.agents import sensitivity as sensitivity_agent
from src.config import LOGS_DIR, MODEL, MODEL_FAST, PROMPTS_DIR
from src.types import (
    CaseStudy,
    CausalGraph,
    Edge,
    Evidence,
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

VALID_ASSET_CLASSES = {"equities", "futures", "commodities", "fx", "rates", "macro"}


@dataclass
class _Candidate:
    """Draft node + the proposed mechanism for the edge that would carry it."""

    label: str
    description: str
    asset_class: Optional[str]
    mechanism: str


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
    model: str,
) -> list[_Candidate]:
    sib_summary = ", ".join(s.label for s in siblings) if siblings else "(none)"
    user = (
        "PROPOSE_CHILDREN.\n"
        f"Case study: {case_study.name} "
        f"({case_study.date_range[0]} to {case_study.date_range[1]}).\n"
        f"Triggering event: {case_study.triggering_event}.\n"
        f"Parent node (layer {parent.layer}): {parent.label}. {parent.description}\n"
        f"Existing siblings under this parent: {sib_summary}.\n\n"
        "Propose 3 to 5 candidate downstream nodes that could plausibly follow from the parent "
        "within the case study window. Cover diverse asset classes and transmission channels. "
        "Each candidate must name an observable downstream variable. "
        "Respond with JSON only, a list of objects:\n"
        '[{"label": "short label", "description": "one or two sentences", '
        '"asset_class": "equities|futures|commodities|fx|rates|macro", '
        '"mechanism": "one sentence parent->child mechanism"}, ...]'
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
) -> CaseStudy:
    """Expand a CaseStudy's triggering event into a 2 to 3 layer DAG.

    Does not mutate `case_study`. Returns a new CaseStudy with `subtree`
    populated. The orchestrator is the only caller."""
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

    layer_nodes: dict[int, list[Node]] = {0: [root]}

    for layer in range(1, max_layers + 1):
        prev_layer = layer_nodes.get(layer - 1, [])
        if not prev_layer:
            break
        new_layer: list[Node] = []

        for parent in prev_layer:
            if len(graph.nodes) >= max_nodes:
                break
            siblings: list[Node] = []
            candidates = _propose_children(parent, case_study, siblings, model=model_fast)

            for cand in candidates:
                if len(graph.nodes) >= max_nodes:
                    break

                cand_id = _new_id(cand.label)
                cand_node = Node(
                    id=cand_id,
                    label=cand.label,
                    description=cand.description,
                    layer=layer,
                    asset_class=cand.asset_class,
                )

                score = sensitivity_agent.score_edge(
                    parent=parent,
                    candidate=cand_node,
                    mechanism=cand.mechanism,
                    case_study=case_study,
                    tools=tools,
                    model=model,
                    run_id=run_id,
                )
                if not score.keep:
                    _log_call(
                        run_id,
                        "drop_after_score",
                        {"parent": parent.id, "candidate": cand.label, "reason": score.keep_reason},
                    )
                    continue

                cand_node.magnitude_estimate = score.magnitude_estimate
                cand_node.evidence = list(score.supporting_data)

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
                    target = next(
                        (s for s in siblings if s.label == target_label),
                        None,
                    )
                    if target is not None:
                        # Keep the higher-confidence node. Merge evidence both ways.
                        target_edge = next(
                            (e for e in graph.edges if e.dst == target.id),
                            None,
                        )
                        if target_edge is not None and score.confidence > target_edge.confidence:
                            # Replace the existing sibling with the new candidate.
                            cand_node.evidence.extend(target.evidence)
                            _drop_node(graph, target.id)
                            siblings.remove(target)
                            new_layer = [n for n in new_layer if n.id != target.id]
                            # Fall through to commit the new candidate.
                        else:
                            # Keep the existing sibling, fold our evidence into it.
                            target.evidence.extend(cand_node.evidence)
                            _log_call(
                                run_id,
                                "merge_into_sibling",
                                {
                                    "parent": parent.id,
                                    "kept": target.label,
                                    "merged": cand.label,
                                },
                            )
                            continue

                edge = Edge(
                    src=parent.id,
                    dst=cand_id,
                    mechanism=score.mechanism_refined,
                    sensitivity=score.sensitivity,
                    confidence=score.confidence,
                    supporting_data=list(score.supporting_data),
                )
                graph.nodes[cand_id] = cand_node
                if not _add_edge_if_dag(graph, edge):
                    graph.nodes.pop(cand_id, None)
                    _log_call(
                        run_id,
                        "drop_cycle",
                        {"parent": parent.id, "candidate": cand.label},
                    )
                    continue

                siblings.append(cand_node)
                new_layer.append(cand_node)

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
