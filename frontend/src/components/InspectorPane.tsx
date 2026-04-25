import { Info } from "lucide-react";
import type { CausalGraph, CaseStudy, Debate } from "../types";
import { Card } from "./ui/Card";
import { EdgeInspector } from "./panels/EdgeInspector";
import { NodeInspector } from "./panels/NodeInspector";

interface Props {
  graph: CausalGraph;
  selection: { kind: "node" | "edge"; id: string } | null;
  caseStudies: CaseStudy[];
  debates: Record<string, Debate>;
  prunedEdgeIds: Set<string>;
}

export function InspectorPane({ graph, selection, caseStudies, debates, prunedEdgeIds }: Props) {
  return (
    <Card
      title="Inspector"
      subtitle={subtitle(selection)}
      bodyClassName="p-3 overflow-y-auto"
    >
      {!selection ? (
        <EmptyState />
      ) : selection.kind === "node" ? (
        renderNode(selection.id, graph, caseStudies)
      ) : (
        renderEdge(selection.id, graph, debates, prunedEdgeIds)
      )}
    </Card>
  );
}

function subtitle(sel: Props["selection"]) {
  if (!sel) return "Click any node or edge in the graph.";
  return sel.kind === "node" ? `Node ${sel.id}` : `Edge ${sel.id}`;
}

function EmptyState() {
  return (
    <div className="flex h-full min-h-[140px] flex-col items-center justify-center text-center">
      <div className="mb-2 inline-flex h-8 w-8 items-center justify-center rounded-full border border-line bg-bg-elev">
        <Info className="h-3.5 w-3.5 text-ink-mute" />
      </div>
      <p className="text-[12px] font-medium text-ink">Nothing selected</p>
      <p className="mt-1 max-w-[260px] text-[11px] text-ink-mute">
        Click a node to see its evidence and attached analogs, or an edge to see the
        mechanism, sensitivity, and adversary debate.
      </p>
    </div>
  );
}

function renderNode(id: string, graph: CausalGraph, caseStudies: CaseStudy[]) {
  const node = graph.nodes[id];
  if (!node) return <EmptyState />;
  const attached = caseStudies.filter((cs) => cs.attaches_to === id);
  return <NodeInspector node={node} attachedAnalogs={attached} />;
}

function renderEdge(
  id: string,
  graph: CausalGraph,
  debates: Record<string, Debate>,
  prunedEdgeIds: Set<string>
) {
  const edge = graph.edges.find((e) => e.id === id);
  if (!edge) return <EmptyState />;
  const src = graph.nodes[edge.src];
  const dst = graph.nodes[edge.dst];
  return (
    <EdgeInspector
      edge={edge}
      src={src}
      dst={dst}
      debate={debates[edge.id]}
      pruned={prunedEdgeIds.has(edge.id)}
    />
  );
}
