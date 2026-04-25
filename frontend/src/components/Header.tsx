import { Activity, GitBranch, Loader2, Pause, Sparkles } from "lucide-react";
import { Badge } from "./ui/Badge";

interface HeaderProps {
  status: "idle" | "running" | "done" | "error";
  model: string;
  nodeCount: number;
  edgeCount: number;
  startedAt?: number;
  finishedAt?: number;
}

export function Header({ status, model, nodeCount, edgeCount, startedAt, finishedAt }: HeaderProps) {
  const elapsedMs =
    startedAt && (finishedAt ?? (status === "running" ? Date.now() : startedAt))
      ? (finishedAt ?? Date.now()) - startedAt
      : 0;
  const elapsed = elapsedMs > 0 ? `${(elapsedMs / 1000).toFixed(1)}s` : "—";

  const tone =
    status === "running"
      ? "accent"
      : status === "done"
      ? "ok"
      : status === "error"
      ? "bad"
      : "muted";
  const StatusIcon =
    status === "running" ? Loader2 : status === "done" ? Sparkles : Pause;

  return (
    <header className="flex shrink-0 items-center justify-between border-b border-line bg-bg-panel px-5 py-3">
      <div className="flex items-center gap-3">
        <div className="relative h-8 w-8 rounded-md bg-gradient-to-br from-accent/40 to-accent-deep/40 ring-1 ring-accent/30">
          <GitBranch className="absolute inset-0 m-auto h-4 w-4 text-accent" />
        </div>
        <div>
          <h1 className="text-sm font-semibold leading-none text-ink">
            Policy Impact Scenario Mapper
          </h1>
          <p className="mt-1 text-[11px] leading-none text-ink-dim">
            Bridgewater AI hackathon · causal DAG with sensitivities, evidence, and adversarial debate
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="hidden items-center gap-3 text-[11px] text-ink-mute md:flex">
          <span className="flex items-center gap-1.5">
            <span className="label-mini">Model</span>
            <span className="font-mono text-ink">{model}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="label-mini">Nodes</span>
            <span className="font-mono text-ink">{nodeCount}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="label-mini">Edges</span>
            <span className="font-mono text-ink">{edgeCount}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="label-mini">Elapsed</span>
            <span className="font-mono text-ink">{elapsed}</span>
          </span>
        </div>
        <Badge tone={tone as "accent" | "ok" | "bad" | "muted"} dot={status !== "idle"}>
          <StatusIcon
            className={`h-3 w-3 ${status === "running" ? "animate-spin" : ""}`}
          />
          <span className="capitalize">{status}</span>
        </Badge>
        <div className="hidden items-center gap-1 text-ink-dim md:flex">
          <Activity className="h-3.5 w-3.5" />
          <span className="text-[10px] uppercase tracking-wider">live</span>
        </div>
      </div>
    </header>
  );
}
