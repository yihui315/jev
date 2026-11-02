/**
 * trading/lib/finance — PURE helpers for formatting and validation.
 *
 * Kept free of React and free of I/O so they are trivially unit-testable and
 * shared by every page. The validation here is the SAME rule the server enforces
 * client-side for an honest early-error: a proposal missing a confirmation id, a
 * bad side/type, or a non-positive quantity can never reach `createPaperOrder`.
 */
import type {
  AccountRiskStatus,
  OrderSide,
  OrderType,
  PaperAccount,
  PaperOrderProposal,
} from "../services/types";

/** Format a number as USD with 2 decimals and thousands separators. */
export function formatUsd(n: number, opts: { sign?: boolean; decimals?: number } = {}): string {
  const decimals = opts.decimals ?? 2;
  const fixed = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  if (opts.sign) return `${n < 0 ? "-" : "+"}$${fixed}`;
  return `$${fixed}`;
}

/** Format a fraction (0.123) as a percentage string ("12.30%"). */
export function formatPct(frac: number, decimals = 2): string {
  return `${(frac * 100).toFixed(decimals)}%`;
}

export type Tone = "up" | "down" | "flat";

/** Map a signed number to a semantic tone for colouring. */
export function pnlTone(n: number): Tone {
  if (n > 0) return "up";
  if (n < 0) return "down";
  return "flat";
}

/** Tailwind tone class for a PnL / change value. */
export function toneClass(n: number): string {
  const t = pnlTone(n);
  return t === "up" ? "text-status-ok" : t === "down" ? "text-status-danger" : "text-muted-foreground";
}

/** Total notional exposure across all positions, quote currency. */
export function grossExposure(account: PaperAccount): number {
  return account.positions.reduce(
    (sum, p) => sum + Math.abs(p.quantity) * p.markPrice,
    0,
  );
}

/** Gross exposure as a fraction of equity. */
export function exposurePct(account: PaperAccount): number {
  if (account.equity <= 0) return 0;
  return grossExposure(account) / account.equity;
}

/** Server risk status -> a tone class for the badge. */
export function riskTone(status: AccountRiskStatus): string {
  if (status === "breach") return "text-status-danger border-status-danger/40 bg-status-danger/10";
  if (status === "warning") return "text-status-warn border-status-warn/40 bg-status-warn/10";
  return "text-status-ok border-status-ok/40 bg-status-ok/10";
}

export const VALID_SIDES: readonly OrderSide[] = ["BUY", "SELL"];
export const VALID_TYPES: readonly OrderType[] = ["MARKET", "LIMIT"];

export interface ValidationResult {
  ok: boolean;
  error?: string;
}

/**
 * Validate a paper-order proposal on the client.
 *
 * Mirrors the server's hard rules so the UI can refuse a bad order before it is
 * sent, and so `createPaperOrder` can throw a `PaperApiError` with code
 * `MISSING_CONFIRMATION` when the human confirmation is absent. A LIMIT order
 * must carry a positive limit price.
 */
export function validateOrderProposal(p: PaperOrderProposal): ValidationResult {
  if (!VALID_SIDES.includes(p.side)) {
    return { ok: false, error: `invalid side: ${String(p.side)}` };
  }
  if (!VALID_TYPES.includes(p.type)) {
    return { ok: false, error: `invalid type: ${String(p.type)}` };
  }
  if (!Number.isFinite(p.quantity) || p.quantity <= 0) {
    return { ok: false, error: "quantity must be a positive number" };
  }
  if (p.type === "LIMIT") {
    if (!Number.isFinite(p.limitPrice as number) || (p.limitPrice as number) <= 0) {
      return { ok: false, error: "LIMIT order requires a positive limit price" };
    }
  }
  if (!p.confirmationId || !p.confirmationId.trim()) {
    return { ok: false, error: "human confirmation is required before submission" };
  }
  return { ok: true };
}
