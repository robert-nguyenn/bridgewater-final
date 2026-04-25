from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

from src.agents._common import extract_json
from src.config import ANTHROPIC_API_KEY, MODEL, PROMPTS_DIR
from src.types import CausalGraph, Edge, Node, ToolBundle

logger = logging.getLogger(__name__)

# Chain-level failure categories. The per-edge Moderator catches single-edge
# issues (mechanism mismatch, hidden assumption, etc.); this verifier catches
# coherence failures that span multiple edges.
CHAIN_FAILURE_CATEGORIES = {
    "sign_inconsistency",   # +/-/+/- across chain doesn't compose
    "magnitude_leap",        # small cause -> large effect without an amplifier
    "equivocation",          # same term means different things across steps
    "time_mismatch",         # short-horizon step feeds long-horizon with no buffer
    "missing_step",          # obvious intermediate variable skipped
}


@dataclass
class ChainVerification:
    ok: bool
    reason: str
    failed_edge_idx: Optional[int] = None
    failure_category: Optional[str] = None
    raw_response: Optional[str] = None


# Bridge edges connect today's first-order nodes to case-study subtree roots
# (mechanism starts with "historical analog: ..."). Their semantics are
# "this past episode informs this present-day variable" — NOT a forward
# causal link. Chain verification must not treat them as causal steps,
# otherwise it flags them as time_mismatch / reverse causation.
_BRIDGE_PREFIX = "historical analog:"


def _is_bridge_edge(edge: Edge) -> bool:
    return bool(edge.mechanism) and edge.mechanism.startswith(_BRIDGE_PREFIX)


def _format_chain(edges: list[Edge], nodes: dict[str, Node]) -> str:
    lines: list[str] = ["Causal chain to verify, ordered left to right.", ""]
    for i, e in enumerate(edges):
        src = nodes.get(e.src)
        dst = nodes.get(e.dst)
        src_label = src.label if src else e.src
        dst_label = dst.label if dst else e.dst
        lines.append(f"Step {i}: [{e.src}] {src_label}  ->  [{e.dst}] {dst_label}")
        if dst and dst.description:
            lines.append(f"  destination description: {dst.description}")
        lines.append(f"  named mechanism: {e.mechanism}")
        lines.append(
            f"  agent sensitivity: {e.sensitivity:.2f}   "
            f"agent confidence: {e.confidence:.2f}"
        )
        lines.append("")
    return "\n".join(lines)


def _parse(text: str) -> ChainVerification:
    parsed = extract_json(text)
    if parsed is None:
        return ChainVerification(ok=False, reason="parse failed", raw_response=text)
    cat = parsed.get("failure_category")
    if cat is not None and cat not in CHAIN_FAILURE_CATEGORIES:
        cat = None
    failed_idx = parsed.get("failed_edge_idx")
    if failed_idx is not None:
        try:
            failed_idx = int(failed_idx)
        except (TypeError, ValueError):
            failed_idx = None
    return ChainVerification(
        ok=bool(parsed.get("ok", False)),
        reason=str(parsed.get("reason", "")),
        failed_edge_idx=failed_idx,
        failure_category=cat,
        raw_response=text,
    )


def run(
    chain: list[Edge],
    *,
    nodes: dict[str, Node],
    model: str = MODEL,
    client: Any = None,
    tools: Optional[ToolBundle] = None,
    run_id: Optional[str] = None,
) -> ChainVerification:
    """Verify a multi-edge chain for cross-step coherence.

    Single-edge "chains" pass trivially since chain-level checks need >= 2
    edges to fire. The per-edge Moderator already covers single-edge logic.
    """
    if not chain:
        return ChainVerification(ok=True, reason="empty chain")
    if len(chain) < 2:
        return ChainVerification(ok=True, reason="single-edge chain (no chain-level checks)")

    system_prompt = (PROMPTS_DIR / "logic_verifier.md").read_text()
    user_text = _format_chain(chain, nodes)

    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )
    text = msg.content[0].text if msg.content else ""
    return _parse(text)


def verify_paths(
    graph: CausalGraph,
    *,
    model: str = MODEL,
    client: Any = None,
    run_id: Optional[str] = None,
    max_paths: int = 20,
    min_path_length: int = 2,
    cutoff: int = 8,
    max_workers: int = 4,
) -> dict[str, ChainVerification]:
    """Walk root-to-leaf paths in the graph and verify each one in parallel.

    Returns a dict keyed by ``"->".join(edge_ids)``. Paths shorter than
    `min_path_length` (in edges) are skipped (covered by per-edge moderator).
    Total work is capped at `max_paths`, prioritizing the longest paths.
    """
    import networkx as nx

    if not graph.root or graph.root not in graph.nodes:
        return {}

    g = nx.DiGraph()
    for nid in graph.nodes:
        g.add_node(nid)
    edges_by_pair: dict[tuple[str, str], Edge] = {}
    for e in graph.edges:
        g.add_edge(e.src, e.dst)
        edges_by_pair[(e.src, e.dst)] = e

    leaves = [n for n in g.nodes if g.out_degree(n) == 0 and n != graph.root]
    raw_paths: list[list[str]] = []
    for leaf in leaves:
        try:
            for path in nx.all_simple_paths(g, source=graph.root, target=leaf, cutoff=cutoff):
                if len(path) - 1 >= min_path_length:
                    raw_paths.append(path)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    raw_paths.sort(key=lambda p: -len(p))
    raw_paths = raw_paths[:max_paths]

    # Walk every path and split at bridge edges (historical-analog links).
    # Today-side and historical-side segments get verified independently so
    # the verifier never grades a chain that crosses time/causality regimes.
    chains: list[tuple[str, list[Edge]]] = []
    seen_keys: set[str] = set()
    for path in raw_paths:
        edges_in_path: list[Edge] = []
        ok = True
        for i in range(len(path) - 1):
            edge = edges_by_pair.get((path[i], path[i + 1]))
            if edge is None:
                ok = False
                break
            edges_in_path.append(edge)
        if not ok:
            continue

        current_segment: list[Edge] = []
        for e in edges_in_path:
            if _is_bridge_edge(e):
                if len(current_segment) >= min_path_length:
                    key = "->".join(seg_e.id for seg_e in current_segment)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        chains.append((key, list(current_segment)))
                current_segment = []
            else:
                current_segment.append(e)
        if len(current_segment) >= min_path_length:
            key = "->".join(seg_e.id for seg_e in current_segment)
            if key not in seen_keys:
                seen_keys.add(key)
                chains.append((key, list(current_segment)))

    results: dict[str, ChainVerification] = {}
    if not chains:
        return results

    def _verify(item):
        key, edges = item
        return key, run(edges, nodes=graph.nodes, model=model, client=client, run_id=run_id)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for fut in as_completed([executor.submit(_verify, c) for c in chains]):
            try:
                key, result = fut.result()
                results[key] = result
            except Exception as exc:
                logger.warning("chain verification failed: %s", exc)
    return results
