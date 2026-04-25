import { clsx } from "clsx";
import type { ReactNode } from "react";

type Tone = "neutral" | "accent" | "ok" | "warn" | "bad" | "muted";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-bg-elev text-ink border-line",
  accent: "bg-accent/10 text-accent border-accent/30",
  ok: "bg-ok/10 text-ok border-ok/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  bad: "bg-bad/10 text-bad border-bad/30",
  muted: "bg-bg-subtle text-ink-mute border-line",
};

export function Badge({
  tone = "neutral",
  children,
  className,
  dot,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium tracking-wide",
        toneClasses[tone],
        className
      )}
    >
      {dot && (
        <span
          className={clsx(
            "h-1.5 w-1.5 rounded-full",
            tone === "accent" && "bg-accent",
            tone === "ok" && "bg-ok",
            tone === "warn" && "bg-warn",
            tone === "bad" && "bg-bad",
            tone === "neutral" && "bg-ink",
            tone === "muted" && "bg-ink-mute"
          )}
        />
      )}
      {children}
    </span>
  );
}
