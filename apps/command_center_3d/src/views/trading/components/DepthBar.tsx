/**
 * trading/components/DepthBar — a single order-book level with a depth fill.
 *
 * The filled width is the level's cumulative `total` normalised against the
 * deepest side of the book, so bids and asks render as mirrored depth bars.
 * Pure SVG/CSS, no charting dependency.
 */
import type { OrderBookLevel } from "../services/types";
import { cn } from "../../../ui/cn";

export function DepthBar({
  level,
  side,
  maxTotal,
}: {
  level: OrderBookLevel;
  side: "bid" | "ask";
  maxTotal: number;
}) {
  const pct = maxTotal > 0 ? Math.min(100, (level.total / maxTotal) * 100) : 0;
  const isBid = side === "bid";
  return (
    <div className="relative flex items-center justify-between px-2 py-0.5 text-xs tabular-nums">
      <div
        className={cn(
          "absolute inset-y-0",
          isBid ? "right-0 bg-status-ok/10" : "right-0 bg-status-danger/10",
          "rounded",
        )}
        style={{ width: `${pct}%` }}
        aria-hidden
      />
      <span className={cn("relative z-10", isBid ? "text-status-ok" : "text-status-danger")}>
        {level.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
      <span className="relative z-10 text-muted-foreground">{level.qty.toFixed(2)}</span>
      <span className="relative z-10 text-muted-foreground">{level.total.toFixed(2)}</span>
    </div>
  );
}
