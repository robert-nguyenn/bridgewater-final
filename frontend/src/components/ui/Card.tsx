import { clsx } from "clsx";
import type { ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}

export function Card({ title, subtitle, right, className, bodyClassName, children }: CardProps) {
  return (
    <section className={clsx("panel flex min-h-0 flex-col", className)}>
      {(title || right || subtitle) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 pb-3 pt-3.5">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-mute">{subtitle}</p>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={clsx("min-h-0 flex-1", bodyClassName ?? "p-4")}>{children}</div>
    </section>
  );
}
