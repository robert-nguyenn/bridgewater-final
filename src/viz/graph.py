from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

import networkx as nx

from src.types import CausalGraph

logger = logging.getLogger(__name__)

LAYER_COLORS = {
    0: "#E63946",  # root: red
    1: "#F4A261",  # first-order: orange
    2: "#2A9D8F",  # second-order: teal
    3: "#264653",  # deeper: dark blue
}
DEFAULT_COLOR = "#999999"


def to_networkx(graph: CausalGraph) -> nx.DiGraph:
    """Convert a CausalGraph into a networkx.DiGraph with attributes attached."""
    g = nx.DiGraph()
    for nid, n in graph.nodes.items():
        g.add_node(
            nid,
            label=n.label,
            description=n.description,
            layer=n.layer,
            asset_class=n.asset_class,
            magnitude_estimate=n.magnitude_estimate,
        )
    for e in graph.edges:
        g.add_edge(
            e.src,
            e.dst,
            mechanism=e.mechanism,
            sensitivity=e.sensitivity,
            confidence=e.confidence,
            edge_id=e.id,
        )
    return g


def _node_color(layer: Optional[int]) -> str:
    if layer is None:
        return DEFAULT_COLOR
    return LAYER_COLORS.get(min(layer, 3), DEFAULT_COLOR)


def render_pyvis(graph: CausalGraph, out_path: Path) -> Path:
    """Render to interactive pyvis HTML. Hover any node/edge for details."""
    try:
        from pyvis.network import Network
    except ImportError as exc:
        raise RuntimeError(
            f"pyvis not installed: {exc}. Run: pip install pyvis"
        ) from exc

    net = Network(
        height="700px",
        width="100%",
        directed=True,
        notebook=False,
        cdn_resources="remote",
    )
    net.set_options(
        '{"physics": {"barnesHut": {"gravitationalConstant": -8000, '
        '"springLength": 200}}, "edges": {"smooth": {"type": "dynamic"}}}'
    )

    for nid, n in graph.nodes.items():
        title_lines = [
            f"<b>{html.escape(n.label)}</b>",
            f"<i>{html.escape(n.description or '')}</i>",
        ]
        if n.asset_class:
            title_lines.append(f"asset class: {html.escape(n.asset_class)}")
        if n.magnitude_estimate is not None:
            title_lines.append(f"magnitude: {n.magnitude_estimate}")
        net.add_node(
            nid,
            label=n.label[:40],
            title="<br>".join(title_lines),
            color=_node_color(n.layer),
            level=n.layer or 0,
        )

    for e in graph.edges:
        if e.src not in graph.nodes or e.dst not in graph.nodes:
            continue
        weight = max(1, int(e.sensitivity * 5))
        title = (
            f"<b>{html.escape(e.mechanism)}</b><br>"
            f"sensitivity: {e.sensitivity:.2f}<br>"
            f"confidence: {e.confidence:.2f}"
        )
        net.add_edge(e.src, e.dst, value=weight, title=title)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(out_path))
    return out_path


def render_graphviz(graph: CausalGraph, out_path: Path) -> Path:
    """Render to a static graphviz file (format inferred from extension)."""
    try:
        import graphviz
    except ImportError as exc:
        raise RuntimeError(
            f"graphviz not installed: {exc}. Run: pip install graphviz"
        ) from exc

    out_path = Path(out_path)
    fmt = out_path.suffix.lstrip(".") or "svg"
    dot = graphviz.Digraph(format=fmt)
    dot.attr(rankdir="LR")

    for nid, n in graph.nodes.items():
        dot.node(
            nid,
            label=n.label[:60],
            style="filled",
            fillcolor=_node_color(n.layer),
            fontcolor="white",
        )

    for e in graph.edges:
        if e.src not in graph.nodes or e.dst not in graph.nodes:
            continue
        label = f"{e.mechanism[:40]}\\n(s={e.sensitivity:.2f}, c={e.confidence:.2f})"
        dot.edge(e.src, e.dst, label=label, penwidth=str(1 + e.sensitivity * 3))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = dot.render(filename=str(out_path.with_suffix("")), cleanup=True)
    return Path(rendered)


__all__ = ["to_networkx", "render_pyvis", "render_graphviz", "LAYER_COLORS"]
