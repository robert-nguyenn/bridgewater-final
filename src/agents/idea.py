from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Optional

from src.config import LOGS_DIR, PROMPTS_DIR
from src.types import Evidence, Node, ToolBundle

ASSET_CLASSES = ("equities", "futures", "commodities", "fx", "rates", "macro")
EVIDENCE_KINDS = ("fred_series", "ticker", "fundamentals", "speech", "article")
MIN_NODES = 3
MAX_NODES = 8
FIRST_ORDER_LAYER = 1


def _load_prompt() -> str:
    return (PROMPTS_DIR / "idea.md").read_text(encoding="utf-8")


SUBMIT_TOOL = {
    "name": "submit_first_order_nodes",
    "description": "Submit the first order causal Nodes for the policy event.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "minItems": MIN_NODES,
                "maxItems": MAX_NODES,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                        "asset_class": {
                            "type": ["string", "null"],
                            "enum": [*ASSET_CLASSES, None],
                        },
                        "magnitude_estimate": {"type": ["number", "null"]},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string", "enum": list(EVIDENCE_KINDS)},
                                    "ref": {"type": "string"},
                                    "note": {"type": ["string", "null"]},
                                },
                                "required": ["kind", "ref"],
                            },
                        },
                    },
                    "required": ["label", "description"],
                },
            }
        },
        "required": ["nodes"],
    },
}


def _normalize_class(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s if s in ASSET_CLASSES else None


def _coerce_evidence(raw: Any) -> list[Evidence]:
    if not raw:
        return []
    out: list[Evidence] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().lower()
        ref = str(item.get("ref", "")).strip()
        if kind not in EVIDENCE_KINDS or not ref:
            continue
        note = item.get("note")
        out.append(Evidence(kind=kind, ref=ref, note=note if note else None))
    return out


def _post_process(raw_nodes: list[dict[str, Any]]) -> list[Node]:
    """Stable ids, dedup labels, validate enums, drop empties, cap at MAX_NODES."""
    out: list[Node] = []
    seen_labels: set[str] = set()
    for raw in raw_nodes:
        label = str(raw.get("label", "")).strip()
        description = str(raw.get("description", "")).strip()
        if not label or not description:
            continue
        key = label.lower()
        if key in seen_labels:
            continue
        seen_labels.add(key)

        magnitude = raw.get("magnitude_estimate")
        if magnitude is not None:
            try:
                magnitude = float(magnitude)
            except (TypeError, ValueError):
                magnitude = None

        out.append(
            Node(
                id=f"n{len(out) + 1}",
                label=label,
                description=description,
                layer=FIRST_ORDER_LAYER,
                asset_class=_normalize_class(raw.get("asset_class")),
                magnitude_estimate=magnitude,
                evidence=_coerce_evidence(raw.get("evidence")),
            )
        )
        if len(out) >= MAX_NODES:
            break
    return out


def _log_call(payload: dict[str, Any]) -> None:
    log_path = LOGS_DIR / "idea.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")


def run(
    event: str,
    *,
    tools: ToolBundle,
    model: str,
    client: Any = None,  # injectable for tests
) -> list[Node]:
    """Generate 3 to 8 candidate first order Nodes from a plain English event.

    See prompts/idea.md for the full contract.

    Args:
        event: plain English policy event, one or two sentences.
        tools: ToolBundle. IdeaAgent does not call tools; reserved for future use.
        model: model id (see src/config.py).
        client: optional injected Anthropic client, for tests.

    Returns:
        list of Node, between 3 and 8 entries, all at layer 1.
    """
    if not event or not event.strip():
        return []

    if client is None:
        from anthropic import Anthropic

        client = Anthropic()

    system_prompt = _load_prompt()
    user_message = json.dumps({"event": event.strip()}, ensure_ascii=False)

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
        tool_choice={"type": "tool", "name": "submit_first_order_nodes"},
        messages=[{"role": "user", "content": user_message}],
    )

    raw_nodes: list[dict[str, Any]] = []
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_first_order_nodes":
            raw_nodes = block.input.get("nodes", [])
            break

    nodes = _post_process(raw_nodes)

    _log_call(
        {
            "agent": "idea",
            "event": event,
            "model": model,
            "n_raw_nodes": len(raw_nodes),
            "n_final_nodes": len(nodes),
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
            "nodes": [asdict(n) for n in nodes],
        }
    )

    return nodes
