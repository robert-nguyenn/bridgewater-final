import { clsx } from "clsx";
import type { ReactNode } from "react";

export interface Tab {
  key: string;
  label: ReactNode;
  count?: number;
}

interface Props {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function Tabs({ tabs, active, onChange, className }: Props) {
  return (
    <div className={clsx("flex items-center gap-1 border-b border-line", className)}>
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            className={clsx(
              "relative -mb-px flex items-center gap-1.5 px-3 py-2 text-[12px] font-medium transition-colors",
              isActive
                ? "text-ink"
                : "text-ink-mute hover:text-ink"
            )}
          >
            {t.label}
            {t.count !== undefined && (
              <span
                className={clsx(
                  "rounded-full px-1.5 py-px font-mono text-[10px]",
                  isActive
                    ? "bg-accent/15 text-accent"
                    : "bg-bg-elev text-ink-dim"
                )}
              >
                {t.count}
              </span>
            )}
            {isActive && (
              <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent" />
            )}
          </button>
        );
      })}
    </div>
  );
}
