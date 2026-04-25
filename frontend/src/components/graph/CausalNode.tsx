import { Handle, Position, type NodeProps } from "@xyflow/react";
import { clsx } from "clsx";
import { ArrowDown, ArrowUp, Database, Minus, Quote } from "lucide-react";
import type { Node as DomainNode } from "../../types";
import { ASSET_COLORS, ASSET_LABELS, pct } from "../../lib/format";

export interface CausalNodeData extends Record<string, unknown> {
  domain: DomainNode;
  selected?: boolean;
  pruned?: boolean;
}

export function CausalNode({ data }: NodeProps) {
  const d = data as unknown as CausalNodeData;
  const n = d.domain;
  const stripeColor = n.asset_class ? ASSET_COLORS[n.asset_class] : "#5eb1ff";
  const isRoot = n.layer === 0;
  const mag = n.magnitude_estimate;
  const dirIcon =
    mag === null || mag === undefined
      ? Minus
      : mag > 0.01
      ? ArrowUp
      : mag < -0.01
      ? ArrowDown
      : Minus;
  const DirIcon = dirIcon;
  const dirTone =
    mag === null || mag === undefined
      ? "text-ink-mute"
      : mag > 0
      ? "text-ok"
      : mag < 0
      ? "text-bad"
      : "text-ink-mute";

  return (
    <div
      className={clsx(
        "group relative w-[230px] overflow-hidden rounded-xl border bg-bg-panel shadow-card transition-all",
        d.selected
          ? "border-accent shadow-glow"
          : "border-line hover:border-line-strong",
        d.pruned && "opacity-40 grayscale"
      )}
      style={{
        // colored vertical accent strip on the left
        boxShadow: d.selected
          ? `inset 4px 0 0 ${stripeColor}, 0 0 0 1px rgba(94,177,255,0.45), 0 6px 30px -12px rgba(94,177,255,0.5)`
          : `inset 4px 0 0 ${stripeColor}`,
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex flex-col gap-1 px-3 pl-4 pt-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="label-mini">
            {isRoot ? "Event" : `L${n.layer}`}
            {n.asset_class ? ` · ${ASSET_LABELS[n.asset_class]}` : ""}
          </span>
          <span className={clsx("flex items-center gap-1 text-[10px] font-mono", dirTone)}>
            <DirIcon className="h-3 w-3" />
            {mag === null || mag === undefined
              ? "—"
              : Math.abs(mag) >= 1
              ? mag.toFixed(2)
              : pct(mag, 1)}
          </span>
        </div>
        <p className="line-clamp-2 text-[12.5px] font-semibold leading-snug text-ink">
          {n.label}
        </p>
        <p className="line-clamp-2 text-[10.5px] leading-snug text-ink-mute">
          {n.description}
        </p>
        <div className="mt-1 flex items-center gap-2 pb-2 text-[10px] text-ink-dim">
          <span className="inline-flex items-center gap-1">
            <Quote className="h-2.5 w-2.5" />
            {n.evidence.length} cite
          </span>
          <span className="inline-flex items-center gap-1">
            <Database className="h-2.5 w-2.5" />
            id {n.id}
          </span>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
