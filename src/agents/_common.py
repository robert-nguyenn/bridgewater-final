from __future__ import annotations

import json
import re
from typing import Any, Optional, Union

from src.types import Edge, Node


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Find the JSON payload. Prefer the last fenced ```json block, else first {...}."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced[-1])
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None


def clamp_score(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, f))


def format_node(node: Node) -> str:
    parts = [
        f"Target: Node id={node.id!r}",
        f"label: {node.label}",
        f"description: {node.description}",
        f"layer: {node.layer}",
    ]
    if node.asset_class:
        parts.append(f"asset_class: {node.asset_class}")
    if node.magnitude_estimate is not None:
        parts.append(f"magnitude_estimate: {node.magnitude_estimate}")
    if node.evidence:
        parts.append("evidence:")
        for ev in node.evidence:
            note = f" ({ev.note})" if ev.note else ""
            parts.append(f"  - {ev.kind}: {ev.ref}{note}")
    else:
        parts.append("evidence: (none cited)")
    return "\n".join(parts)


def format_edge(edge: Edge, nodes: Optional[dict[str, Node]] = None) -> str:
    nodes = nodes or {}
    src = nodes.get(edge.src)
    dst = nodes.get(edge.dst)
    src_label = src.label if src else edge.src
    dst_label = dst.label if dst else edge.dst
    src_desc = f" — {src.description}" if src and src.description else ""
    dst_desc = f" — {dst.description}" if dst and dst.description else ""
    parts = [
        f"Target: Edge id={edge.id!r}",
        f"source: [{edge.src}] {src_label}{src_desc}",
        f"destination: [{edge.dst}] {dst_label}{dst_desc}",
        f"named mechanism: {edge.mechanism}",
        f"agent sensitivity: {edge.sensitivity:.2f}",
        f"agent confidence: {edge.confidence:.2f}",
    ]
    if edge.supporting_data:
        parts.append("supporting_data:")
        for ev in edge.supporting_data:
            note = f" ({ev.note})" if ev.note else ""
            parts.append(f"  - {ev.kind}: {ev.ref}{note}")
    else:
        parts.append("supporting_data: (none cited)")
    return "\n".join(parts)


def format_target(
    target: Union[Node, Edge], nodes: Optional[dict[str, Node]] = None
) -> str:
    if isinstance(target, Node):
        return format_node(target)
    return format_edge(target, nodes)


def target_id(target: Union[Node, Edge]) -> str:
    return target.id
