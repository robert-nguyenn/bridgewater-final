import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { clsx } from "clsx";
import type { Edge as DomainEdge } from "../../types";
import { confidenceColor, sensitivityColor } from "../../lib/format";

export interface CausalEdgeData extends Record<string, unknown> {
  domain: DomainEdge;
  selected?: boolean;
  pruned?: boolean;
  flowing?: boolean;
}

export function CausalEdge(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition } = props;
  const data = props.data as unknown as CausalEdgeData;
  const e = data.domain;

  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.25,
  });

  const stroke = sensitivityColor(e.sensitivity);
  const width = 1 + e.sensitivity * 2.5;
  const opacity = data.pruned ? 0.18 : data.selected ? 1 : 0.78;

  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        style={{
          stroke,
          strokeWidth: data.selected ? width + 1.2 : width,
          opacity,
          strokeDasharray: data.pruned ? "4 4" : undefined,
        }}
        className={clsx(data.flowing && !data.pruned && "edge-flow")}
      />
      <EdgeLabelRenderer>
        <div
          className={clsx(
            "pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2 rounded-md border px-1.5 py-0.5 font-mono text-[9px] leading-none transition-colors",
            data.selected
              ? "border-accent bg-bg-panel text-accent shadow-glow"
              : "border-line bg-bg-panel text-ink-mute hover:text-ink"
          )}
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            color: data.selected ? undefined : confidenceColor(e.confidence),
          }}
          title={e.mechanism}
        >
          s {Math.round(e.sensitivity * 100)} · c {Math.round(e.confidence * 100)}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
