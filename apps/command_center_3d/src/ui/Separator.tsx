import * as React from "react";
import { cn } from "./cn";

/**
 * Separator — a 1px rule. Kept as a plain element rather than
 * `@radix-ui/react-separator` because it is purely decorative here and a
 * screen reader should not announce it.
 */
export function Separator({
  className,
  orientation = "horizontal",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  orientation?: "horizontal" | "vertical";
}) {
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn(
        "shrink-0 bg-border",
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        className,
      )}
      {...props}
    />
  );
}
