/**
 * RoleCard.test.ts — component LOGIC (pure, no DOM/jsdom needed).
 *
 * Pins the six-role label map and the shared finance helpers used across the
 * trading pages. Kept pure so the ordering/label contract is verified without
 * mounting React.
 */
import { describe, it, expect } from "vitest";
import { ROLE_LABELS } from "./RoleCard";
import {
  exposurePct,
  formatPct,
  formatUsd,
  pnlTone,
  riskStatusOf,
  riskTone,
  validateOrderProposal,
} from "../lib/finance";
import type { AccountSummary, AiRoleId } from "../services/types";

const ROLES: AiRoleId[] = ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"];

describe("RoleCard role labels", () => {
  it("provides a Chinese label for every role", () => {
    for (const r of ROLES) {
      expect(ROLE_LABELS[r]).toBeTruthy();
    }
  });
});

describe("finance formatting", () => {
  it("formats USD with sign", () => {
    expect(formatUsd(1234.5)).toBe("$1,234.50");
    expect(formatUsd(-12.345, { sign: true })).toBe("-$12.35");
    expect(formatUsd(12.345, { sign: true })).toBe("+$12.35");
  });

  it("formats percentages from a fraction", () => {
    expect(formatPct(0.1234)).toBe("12.34%");
    expect(formatPct(-0.05)).toBe("-5.00%");
  });

  it("maps a signed number to a tone", () => {
    expect(pnlTone(1)).toBe("up");
    expect(pnlTone(-1)).toBe("down");
    expect(pnlTone(0)).toBe("flat");
  });

  it("maps risk status to a tone class", () => {
    expect(riskTone("ok")).toContain("text-status-ok");
    expect(riskTone("warning")).toContain("text-status-warn");
    expect(riskTone("breach")).toContain("text-status-danger");
  });

  it("derives risk status from the server summary", () => {
    const base: AccountSummary = {
      equity: 10000, cash: 5000, availableCash: 5000, reservedCash: 0,
      marketValue: 5000, unrealizedPnl: 0, realizedPnl: 0, totalPnl: 0,
      dailyPnl: 0, drawdown: 0, exposurePct: 0.2,
      positions: [], dailyLossLocked: false, drawdownLocked: false,
    };
    expect(riskStatusOf(base)).toBe("ok");
    expect(riskStatusOf({ ...base, exposurePct: 0.35 })).toBe("warning");
    expect(riskStatusOf({ ...base, dailyLossLocked: true })).toBe("breach");
    expect(riskStatusOf({ ...base, drawdownLocked: true })).toBe("breach");
  });

  it("exposurePct mirrors the server-derived fraction", () => {
    expect(exposurePct({ ...({} as AccountSummary), exposurePct: 0.42 })).toBeCloseTo(0.42, 6);
  });
});

describe("validateOrderProposal — the client-side gate", () => {
  const base = {
    symbol: "BTCUSDT" as const,
    side: "BUY" as const,
    type: "LIMIT" as const,
    quantity: 0.1,
    limitPrice: 60000,
    confirmationId: "cfm_x",
    clientOrderId: "cli_x",
  };

  it("accepts a fully-specified, confirmed proposal", () => {
    expect(validateOrderProposal(base).ok).toBe(true);
  });

  it("rejects a missing confirmation", () => {
    const r = validateOrderProposal({ ...base, confirmationId: "" });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/confirmation/i);
  });

  it("rejects a non-positive quantity", () => {
    expect(validateOrderProposal({ ...base, quantity: 0 }).ok).toBe(false);
  });

  it("rejects a LIMIT order without a positive limit price", () => {
    expect(validateOrderProposal({ ...base, limitPrice: 0 }).ok).toBe(false);
    expect(validateOrderProposal({ ...base, limitPrice: undefined }).ok).toBe(false);
  });

  it("rejects invalid side/type", () => {
    // @ts-expect-error exercising the runtime guard
    expect(validateOrderProposal({ ...base, side: "HODL" }).ok).toBe(false);
    // @ts-expect-error exercising the runtime guard
    expect(validateOrderProposal({ ...base, type: "STOP" }).ok).toBe(false);
  });
});
