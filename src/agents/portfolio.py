from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from src.config import LOGS_DIR, PROMPTS_DIR
from src.types import CausalGraph, Edge, Node, ToolBundle

ASSET_CLASSES = ("equities", "futures", "commodities", "fx", "rates", "macro")
DIRECTIONS = ("up", "down", "mixed")
MAGNITUDE_LABELS = ("small", "moderate", "large", "unclear")
UNCLASSIFIED = "unclassified"


@dataclass
class PortfolioImpact:
    asset_class: str  # equities | futures | commodities | fx | rates | macro
    direction: str  # up | down | mixed
    summary: str
    contributing_nodes: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    magnitude_label: str = "unclear"  # small | moderate | large | unclear
    confidence: float = 0.0
    key_drivers: list[str] = field(default_factory=list)
    offsets: list[str] = field(default_factory=list)
    time_horizon_days: int = 90


def _load_prompt() -> str:
    return (PROMPTS_DIR / "portfolio.md").read_text(encoding="utf-8")


def _normalize_class(raw: Optional[str]) -> str:
    if not raw:
        return UNCLASSIFIED
    s = raw.strip().lower()
    return s if s in ASSET_CLASSES else UNCLASSIFIED


def _bucket_terminals(terminals: list[Node]) -> dict[str, list[Node]]:
    buckets: dict[str, list[Node]] = defaultdict(list)
    for n in terminals:
        buckets[_normalize_class(n.asset_class)].append(n)
    return dict(buckets)


def _edge_stats(graph: Optional[CausalGraph]) -> dict[str, dict[str, float]]:
    """Aggregate inbound edge stats per node id, for the model to anchor confidence."""
    if graph is None or not graph.edges:
        return {}
    inbound: dict[str, list[Edge]] = defaultdict(list)
    for e in graph.edges:
        inbound[e.dst].append(e)
    stats: dict[str, dict[str, float]] = {}
    for node_id, edges in inbound.items():
        if not edges:
            continue
        n = len(edges)
        stats[node_id] = {
            "avg_confidence": round(sum(e.confidence for e in edges) / n, 4),
            "avg_sensitivity": round(sum(e.sensitivity for e in edges) / n, 4),
            "n_inbound": n,
        }
    return stats


def _node_payload(n: Node) -> dict[str, Any]:
    return {
        "id": n.id,
        "label": n.label,
        "description": n.description,
        "magnitude_estimate": n.magnitude_estimate,
        "evidence": [{"kind": ev.kind, "ref": ev.ref} for ev in (n.evidence or [])],
    }


def _build_user_message(
    seed_event: str,
    buckets: dict[str, list[Node]],
    edge_stats: dict[str, dict[str, float]],
) -> str:
    return json.dumps(
        {
            "seed_event": seed_event,
            "terminals_by_class": {
                cls: [_node_payload(n) for n in nodes] for cls, nodes in buckets.items()
            },
            "edge_stats_by_node": edge_stats,
        },
        ensure_ascii=False,
        default=str,
    )


SUBMIT_TOOL = {
    "name": "submit_portfolio_impacts",
    "description": "Submit per asset class portfolio impact summaries.",
    "input_schema": {
        "type": "object",
        "properties": {
            "impacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_class": {"type": "string", "enum": list(ASSET_CLASSES)},
                        "direction": {"type": "string", "enum": list(DIRECTIONS)},
                        "tickers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 6,
                        },
                        "magnitude_label": {"type": "string", "enum": list(MAGNITUDE_LABELS)},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "summary": {"type": "string"},
                        "key_drivers": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                        "offsets": {"type": "array", "items": {"type": "string"}},
                        "time_horizon_days": {"type": "integer", "minimum": 7, "maximum": 365},
                        "contributing_nodes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "asset_class",
                        "direction",
                        "summary",
                        "magnitude_label",
                        "confidence",
                        "time_horizon_days",
                        "contributing_nodes",
                    ],
                },
            }
        },
        "required": ["impacts"],
    },
}


def _post_process(
    raw_impacts: list[dict[str, Any]],
    *,
    valid_node_ids: set[str],
    valid_classes_in_input: set[str],
) -> list[PortfolioImpact]:
    """Validate enum fields, drop hallucinated node ids and asset classes."""
    out: list[PortfolioImpact] = []
    seen_classes: set[str] = set()
    for imp in raw_impacts:
        cls = _normalize_class(imp.get("asset_class"))
        if cls == UNCLASSIFIED or cls not in valid_classes_in_input:
            continue  # model invented an item for an asset class we did not pass
        if cls in seen_classes:
            continue  # collapse duplicates, first wins
        seen_classes.add(cls)

        direction = imp.get("direction", "mixed")
        if direction not in DIRECTIONS:
            direction = "mixed"
        magnitude = imp.get("magnitude_label", "unclear")
        if magnitude not in MAGNITUDE_LABELS:
            magnitude = "unclear"
        try:
            confidence = max(0.0, min(1.0, float(imp.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            horizon = int(imp.get("time_horizon_days", 90))
        except (TypeError, ValueError):
            horizon = 90
        horizon = max(7, min(365, horizon))

        contributing = [nid for nid in (imp.get("contributing_nodes") or []) if nid in valid_node_ids]
        drivers = [nid for nid in (imp.get("key_drivers") or []) if nid in valid_node_ids][:3]
        offsets = [nid for nid in (imp.get("offsets") or []) if nid in valid_node_ids]
        # drivers/offsets must also be in contributing
        contrib_set = set(contributing)
        drivers = [d for d in drivers if d in contrib_set]
        offsets = [o for o in offsets if o in contrib_set]

        tickers = [str(t).strip().upper() for t in (imp.get("tickers") or []) if str(t).strip()][:6]

        out.append(
            PortfolioImpact(
                asset_class=cls,
                direction=direction,
                summary=str(imp.get("summary", "")).strip(),
                contributing_nodes=contributing,
                tickers=tickers,
                magnitude_label=magnitude,
                confidence=confidence,
                key_drivers=drivers,
                offsets=offsets,
                time_horizon_days=horizon,
            )
        )
    return out


def _log_call(payload: dict[str, Any]) -> None:
    log_path = LOGS_DIR / "portfolio.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")


def run(
    terminals: list[Node],
    *,
    tools: ToolBundle,
    model: str,
    graph: Optional[CausalGraph] = None,
    seed_event: str = "",
    client: Any = None,  # injectable for tests
) -> list[PortfolioImpact]:
    """Translate terminal nodes into per asset class portfolio impacts.

    See prompts/portfolio.md for the contract.

    Args:
        terminals: list of terminal Nodes from the merged causal graph.
        tools: ToolBundle (currently unused; reserved for tools.yahoo lookup).
        model: model id (see src/config.py).
        graph: optional full graph, used only to compute inbound edge stats per
            terminal so the model can anchor confidence.
        seed_event: original policy event in plain English, for context.
        client: optional injected Anthropic client, for tests.

    Returns:
        list of PortfolioImpact, at most one per asset class, only for classes
        that had at least one contributing terminal.
    """
    if not terminals:
        return []

    buckets = _bucket_terminals(terminals)
    classes_in_input = {c for c in buckets if c != UNCLASSIFIED}
    if not classes_in_input:
        return []
    edge_stats = _edge_stats(graph)
    valid_node_ids = {n.id for n in terminals}

    if client is None:
        from anthropic import Anthropic

        client = Anthropic()

    system_prompt = _load_prompt()
    user_message = _build_user_message(seed_event, buckets, edge_stats)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[SUBMIT_TOOL],
        tool_choice={"type": "tool", "name": "submit_portfolio_impacts"},
        messages=[{"role": "user", "content": user_message}],
    )

    raw_impacts: list[dict[str, Any]] = []
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_portfolio_impacts":
            raw_impacts = block.input.get("impacts", [])
            break

    impacts = _post_process(
        raw_impacts,
        valid_node_ids=valid_node_ids,
        valid_classes_in_input=classes_in_input,
    )

    _log_call(
        {
            "agent": "portfolio",
            "seed_event": seed_event,
            "model": model,
            "n_terminals": len(terminals),
            "asset_classes_in_input": sorted(classes_in_input),
            "n_raw_impacts": len(raw_impacts),
            "n_final_impacts": len(impacts),
            "usage": getattr(response, "usage", None) and {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(
                    response.usage, "cache_read_input_tokens", None
                ),
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", None
                ),
            },
            "impacts": [asdict(i) for i in impacts],
        }
    )

    return impacts
