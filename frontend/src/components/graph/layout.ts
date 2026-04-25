import dagre from "dagre";
import { Position, type Edge, type Node as RFNode } from "@xyflow/react";

export interface LayoutOptions {
  nodeWidth?: number;
  nodeHeight?: number;
  rankSep?: number;
  nodeSep?: number;
  direction?: "LR" | "TB";
}

export function layoutGraph<T extends Record<string, unknown>>(
  nodes: RFNode<T>[],
  edges: Edge[],
  opts: LayoutOptions = {}
): { nodes: RFNode<T>[]; edges: Edge[] } {
  const {
    nodeWidth = 230,
    nodeHeight = 96,
    rankSep = 90,
    nodeSep = 36,
    direction = "LR",
  } = opts;

  const g = new dagre.graphlib.Graph({ multigraph: false, compound: false });
  g.setGraph({ rankdir: direction, ranksep: rankSep, nodesep: nodeSep, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    g.setNode(n.id, { width: nodeWidth, height: nodeHeight });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  const sourcePosition = direction === "LR" ? Position.Right : Position.Bottom;
  const targetPosition = direction === "LR" ? Position.Left : Position.Top;

  const nextNodes: RFNode<T>[] = nodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: pos
        ? { x: pos.x - nodeWidth / 2, y: pos.y - nodeHeight / 2 }
        : n.position,
      sourcePosition,
      targetPosition,
    };
  });
  return { nodes: nextNodes, edges };
}
