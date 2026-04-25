import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Edge as RFEdge,
  type Node as RFNode,
} from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";
import { ASSET_COLORS } from "../../lib/format";
import type { CausalGraph } from "../../types";
import { CausalEdge, type CausalEdgeData } from "./CausalEdge";
import { CausalNode, type CausalNodeData } from "./CausalNode";
import { layoutGraph } from "./layout";

const nodeTypes = { causal: CausalNode };
const edgeTypes = { causal: CausalEdge };

// Re-fits the viewport whenever the graph grows. Lives inside ReactFlow so it
// can use useReactFlow(). The user can still pan/zoom freely; we only refit
// when node/edge count changes (i.e. new content has streamed in).
function AutoFit({ nodeCount, edgeCount }: { nodeCount: number; edgeCount: number }) {
  const rf = useReactFlow();
  const lastSig = useRef("");
  useEffect(() => {
    const sig = `${nodeCount}:${edgeCount}`;
    if (sig === lastSig.current) return;
    lastSig.current = sig;
    if (nodeCount === 0) return;
    const id = window.setTimeout(() => {
      rf.fitView({ padding: 0.18, maxZoom: 1.1, duration: 320 });
    }, 60);
    return () => window.clearTimeout(id);
  }, [rf, nodeCount, edgeCount]);
  return null;
}

interface Selection {
  kind: "node" | "edge";
  id: string;
}

interface Props {
  graph: CausalGraph;
  prunedEdgeIds: Set<string>;
  prunedNodeIds: Set<string>;
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
  // ids to render with the "flowing dash" animation (e.g. just-added edges)
  flowingEdgeIds?: Set<string>;
}

export function GraphCanvas({
  graph,
  prunedEdgeIds,
  prunedNodeIds,
  selection,
  onSelect,
  flowingEdgeIds,
}: Props) {
  // Persist user-dragged positions across re-layouts. The simulator re-emits
  // nodes as it streams, so each render we recompute layout but keep any
  // node the user has manually moved at its dragged position.
  const userPositions = useRef<Map<string, { x: number; y: number }>>(new Map());

  const { rfNodes, rfEdges } = useMemo(() => {
    const baseNodes: RFNode<CausalNodeData>[] = Object.values(graph.nodes).map((n) => ({
      id: n.id,
      type: "causal",
      position: { x: 0, y: 0 },
      data: {
        domain: n,
        selected: selection?.kind === "node" && selection.id === n.id,
        pruned: prunedNodeIds.has(n.id),
      },
      draggable: true,
      selectable: true,
    }));
    const baseEdges: RFEdge<CausalEdgeData>[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.src,
      target: e.dst,
      type: "causal",
      data: {
        domain: e,
        selected: selection?.kind === "edge" && selection.id === e.id,
        pruned: prunedEdgeIds.has(e.id),
        flowing: flowingEdgeIds?.has(e.id) ?? false,
      },
    }));
    const laid = layoutGraph(baseNodes, baseEdges, {
      direction: "LR",
      rankSep: 110,
      nodeSep: 38,
    });
    // overlay user-dragged positions
    const finalNodes = laid.nodes.map((n) => {
      const u = userPositions.current.get(n.id);
      return u ? { ...n, position: u } : n;
    });
    return { rfNodes: finalNodes, rfEdges: laid.edges };
  }, [graph, prunedEdgeIds, prunedNodeIds, selection, flowingEdgeIds]);

  const isEmpty = rfNodes.length === 0;

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.18, maxZoom: 1.1 }}
        minZoom={0.25}
        maxZoom={1.6}
        proOptions={{ hideAttribution: false }}
        nodesDraggable
        elementsSelectable
        onNodeDragStop={(_e, node) => {
          userPositions.current.set(node.id, node.position);
        }}
        onNodeClick={(_e, n) => onSelect({ kind: "node", id: n.id })}
        onEdgeClick={(_e, ed) => onSelect({ kind: "edge", id: ed.id })}
        onPaneClick={() => onSelect(null)}
      >
        <AutoFit nodeCount={rfNodes.length} edgeCount={rfEdges.length} />
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#1f2738" />
        <MiniMap
          pannable
          zoomable
          maskColor="rgba(10,13,20,0.7)"
          nodeColor={(n) => {
            const dn = (n.data as CausalNodeData | undefined)?.domain;
            return dn?.asset_class ? ASSET_COLORS[dn.asset_class] : "#5eb1ff";
          }}
          nodeStrokeWidth={2}
        />
        <Controls position="bottom-right" showInteractive={false} />
      </ReactFlow>

      {isEmpty && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="max-w-sm text-center">
            <div className="mx-auto mb-3 h-10 w-10 rounded-full border border-line bg-bg-elev" />
            <p className="text-sm font-medium text-ink">No graph yet</p>
            <p className="mt-1 text-xs text-ink-mute">
              Enter a policy event on the left and click <span className="kbd">Map impact</span> to
              stream the causal DAG.
            </p>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="pointer-events-none absolute left-3 top-3 flex flex-col gap-2 rounded-lg border border-line bg-bg-panel/85 px-2.5 py-2 backdrop-blur">
        <div>
          <div className="label-mini mb-1">Asset class</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10.5px]">
            {(Object.entries(ASSET_COLORS) as [keyof typeof ASSET_COLORS, string][]).map(
              ([cls, color]) => (
                <div key={cls} className="flex items-center gap-1.5 text-ink-mute">
                  <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
                  <span className="capitalize">{cls}</span>
                </div>
              )
            )}
          </div>
        </div>
        <div className="border-t border-line pt-1.5 text-[10px] text-ink-dim">
          Edge label · <span className="font-mono text-ink">s</span> sensitivity ·{" "}
          <span className="font-mono text-ink">c</span> confidence
        </div>
      </div>
    </div>
  );
}
