import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./cn";

/**
 * Badge — the semantic status chip.
 *
 * The `status` variants are the enforcement point for the project's honesty
 * rule: a state the system cannot observe renders as `unknown` (grey), and
 * there is no variant that makes an unknown state look healthy.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium leading-none transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/15 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        destructive: "border-transparent bg-destructive/15 text-destructive",
        ok: "border-transparent bg-status-ok/15 text-status-ok",
        live: "border-transparent bg-status-live/15 text-status-live",
        warn: "border-transparent bg-status-warn/15 text-status-warn",
        danger: "border-transparent bg-status-danger/15 text-status-danger",
        info: "border-transparent bg-status-info/15 text-status-info",
        /** UNKNOWN is grey. Never green. */
        unknown: "border-transparent bg-status-unknown/15 text-status-unknown",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
