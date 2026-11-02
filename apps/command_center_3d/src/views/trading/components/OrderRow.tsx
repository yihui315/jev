/**
 * trading/components/OrderRow — one row in the orders / audit table.
 *
 * Displays exactly the server fields: orderId, symbol, side, type, quantity,
 * limit price, status, filled qty, confirmation id. No browser-derived values.
 */
import type { PaperOrder } from "../services/types";
import { cn } from "../../../ui/cn";
import { formatUsd } from "../lib/finance";

const STATUS_TONE: Record<PaperOrder["status"], string> = {
  PENDING_CONFIRMATION: "text-status-warn",
  SUBMITTED: "text-status-warn",
  PARTIAL: "text-status-warn",
  FILLED: "text-status-ok",
  CANCELLED: "text-muted-foreground",
  REJECTED: "text-status-danger",
};

export function OrderRow({ order }: { order: PaperOrder }) {
  return (
    <tr className="border-b border-border text-sm">
      <td className="px-3 py-2 font-mono text-muted-foreground">{order.orderId}</td>
      <td className="px-3 py-2">{order.symbol}</td>
      <td className={cn("px-3 py-2 font-medium", order.side === "BUY" ? "text-status-ok" : "text-status-danger")}>
        {order.side}
      </td>
      <td className="px-3 py-2">{order.type}</td>
      <td className="px-3 py-2 tabular-nums">{order.quantity}</td>
      <td className="px-3 py-2 tabular-nums">{order.limitPrice ? formatUsd(order.limitPrice) : "—"}</td>
      <td className={cn("px-3 py-2 font-medium", STATUS_TONE[order.status])}>{order.status}</td>
      <td className="px-3 py-2 tabular-nums">{order.filledQty}</td>
      <td className="px-3 py-2 font-mono text-muted-foreground">{order.confirmationId}</td>
    </tr>
  );
}
