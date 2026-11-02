/**
 * trading/components/StatCard — a labelled metric tile.
 *
 * `tone` colours the value; pass a signed number and it picks up/down/flat
 * automatically via finance.ts helpers. Pure presentational, no data fetching.
 */
import type { ReactNode } from "react";
import { cn } from "../../../ui/cn";
import { toneClass } from "../lib/finance";

export function StatCard({
  label,
  value,
  hint,
  tone,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** "auto" derives the tone from a signed number passed via `value` number. */
  tone?: "auto" | "up" | "down" | "flat" | "neutral";
  className?: string;
}) {
  let valueClass = "text-foreground";
  if (tone === "auto" && typeof value === "number") valueClass = toneClass(value as number);
  else if (tone === "up") valueClass = "text-status-ok";
  else if (tone === "down") valueClass = "text-status-danger";
  else if (tone === "neutral") valueClass = "text-muted-foreground";

  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-card p-4",
        className,
      )}
    >
      <div className="t-meta text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold tabular-nums", valueClass)}>{value}</div>
      {hint ? <div className="mt-1 t-meta text-muted-foreground">{hint}</div> : null}
    </div>
  );
}
