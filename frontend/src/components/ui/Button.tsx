import { clsx } from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const variantStyles: Record<Variant, string> = {
  primary:
    "bg-accent text-bg-base hover:bg-accent-glow focus:ring-accent disabled:bg-accent/40 disabled:text-bg-base/60",
  secondary:
    "bg-bg-elev text-ink border border-line hover:border-line-strong hover:bg-bg-subtle focus:ring-accent/60",
  ghost:
    "bg-transparent text-ink-mute hover:text-ink hover:bg-bg-elev focus:ring-accent/40",
  danger:
    "bg-bad/15 text-bad border border-bad/40 hover:bg-bad/25 focus:ring-bad/40",
};

const sizeStyles: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  leadingIcon,
  trailingIcon,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      className={clsx(
        "inline-flex select-none items-center justify-center rounded-md font-medium transition-colors focus:outline-none focus:ring-2 disabled:cursor-not-allowed",
        variantStyles[variant],
        sizeStyles[size],
        className
      )}
    >
      {leadingIcon && <span className="shrink-0">{leadingIcon}</span>}
      {children}
      {trailingIcon && <span className="shrink-0">{trailingIcon}</span>}
    </button>
  );
}
