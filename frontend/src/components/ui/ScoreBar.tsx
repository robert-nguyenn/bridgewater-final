import { clsx } from "clsx";

interface Props {
  value: number; // 0..1
  label?: string;
  tone?: "accent" | "ok" | "warn" | "bad" | "auto";
  className?: string;
  showValue?: boolean;
}

function autoTone(v: number): "bad" | "warn" | "accent" | "ok" {
  if (v < 0.3) return "bad";
  if (v < 0.55) return "warn";
  if (v < 0.8) return "accent";
  return "ok";
}

export function ScoreBar({ value, label, tone = "auto", className, showValue = true }: Props) {
  const t = tone === "auto" ? autoTone(value) : tone;
  const fill = Math.max(0, Math.min(1, value));
  return (
    <div className={clsx("w-full", className)}>
      {(label || showValue) && (
        <div className="mb-1 flex items-center justify-between text-[11px]">
          {label && <span className="text-ink-mute">{label}</span>}
          {showValue && (
            <span className="font-mono text-ink">{(value * 100).toFixed(0)}%</span>
          )}
        </div>
      )}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-subtle">
        <div
          className={clsx(
            "h-full rounded-full transition-all",
            t === "ok" && "bg-ok",
            t === "accent" && "bg-accent",
            t === "warn" && "bg-warn",
            t === "bad" && "bg-bad"
          )}
          style={{ width: `${fill * 100}%` }}
        />
      </div>
    </div>
  );
}
