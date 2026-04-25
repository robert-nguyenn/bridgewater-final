import { Check, ChevronRight, CircleDashed, CircleDot, Loader2, Minus } from "lucide-react";
import { clsx } from "clsx";
import type { Stage } from "../types";
import { Card } from "./ui/Card";

export function PipelineStages({ stages }: { stages: Stage[] }) {
  const done = stages.filter((s) => s.status === "done").length;
  const active = stages.find((s) => s.status === "active");
  const subtitle = active
    ? `running stage ${active.id}: ${active.label}`
    : `${done}/${stages.length} stages complete`;

  return (
    <Card title="Pipeline" subtitle={subtitle} bodyClassName="p-2.5">
      <ol className="flex flex-col gap-0.5">
        {stages.map((s, idx) => (
          <StageRow key={s.id} stage={s} last={idx === stages.length - 1} />
        ))}
      </ol>
    </Card>
  );
}

function StageRow({ stage, last }: { stage: Stage; last: boolean }) {
  const Icon = iconFor(stage.status);
  return (
    <li
      className={clsx(
        "group relative flex items-start gap-2.5 rounded-md px-2 py-2 transition-colors",
        stage.status === "active" && "bg-accent/5",
        stage.status === "done" && "opacity-90",
        stage.status === "skipped" && "opacity-60"
      )}
    >
      <div className="relative mt-0.5 flex flex-col items-center">
        <span
          className={clsx(
            "flex h-5 w-5 items-center justify-center rounded-full border",
            stage.status === "pending" && "border-line text-ink-dim",
            stage.status === "active" && "border-accent/60 bg-accent/15 text-accent shadow-glow",
            stage.status === "done" && "border-ok/40 bg-ok/15 text-ok",
            stage.status === "skipped" && "border-line bg-bg-subtle text-ink-dim",
            stage.status === "error" && "border-bad/50 bg-bad/15 text-bad"
          )}
        >
          <Icon
            className={clsx(
              "h-3 w-3",
              stage.status === "active" && "animate-spin"
            )}
          />
        </span>
        {!last && <span className="mt-1 h-full w-px flex-1 bg-line" />}
      </div>
      <div className="min-w-0 flex-1 pb-1">
        <div className="flex items-center justify-between gap-2">
          <p
            className={clsx(
              "text-[12.5px] font-medium leading-tight",
              stage.status === "active" ? "text-ink" : "text-ink"
            )}
          >
            <span className="text-ink-dim">{String(stage.id).padStart(2, "0")} ·</span>{" "}
            {stage.label}
          </p>
          {stage.metric && (
            <span className="shrink-0 font-mono text-[10px] text-ink-mute">
              {stage.metric}
            </span>
          )}
        </div>
        <p className="mt-0.5 line-clamp-2 text-[11px] text-ink-mute">{stage.description}</p>
      </div>
      <ChevronRight className="mt-2 hidden h-3 w-3 shrink-0 text-ink-dim group-hover:block" />
    </li>
  );
}

function iconFor(status: Stage["status"]) {
  switch (status) {
    case "active":
      return Loader2;
    case "done":
      return Check;
    case "skipped":
      return Minus;
    case "error":
      return CircleDot;
    default:
      return CircleDashed;
  }
}
