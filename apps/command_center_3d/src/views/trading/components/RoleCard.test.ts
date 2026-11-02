/**
 * RoleCard.test.ts — component LOGIC (pure, no DOM/jsdom needed).
 *
 * Tests the exported pure helpers that drive the Agent Center rendering: the
 * six-role ordering and the label map. Keeping these pure means the card's
 * ordering contract is pinned without mounting React in a browser. Also covers
 * the shared finance helpers used across the trading pages.
 */
import { describe, it, expect } from "vitest";
import { ROLE_LABELS, roleIndex } from "./RoleCard";
import {
  exposurePct,
  formatPct,
  formatUsd,
  grossExposure,
  pnlTone,
  riskTone,
  validateOrderProposal,
} from "../lib/finance";
import type { PaperAccount, ResearchRole } from "../services/types";

const ROLES: ResearchRole[] = ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"];

describe("RoleCard role ordering", () => {
  it("orders the six roles SPOTTER → CLOSER", () => {
    expect(ROLES.map(roleIndex)).toEqual([0, 1, 2, 3, 4, 5]);
  });

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
});

describe("account exposure (server-derived positions)", () => {
  const account: PaperAccount = {
    cash: 1000,
    equity: 2000,
    marginUsed: 500,
    positions: [
      { symbol: "BTCUSDT", side: "long", quantity: 1, avgPrice: 100, markPrice: 120, unrealizedPnl: 20, leverage: 2 },
      { symbol: "ETHUSDT", side: "short", quantity: 2, avgPrice: 50, markPrice: 40, unrealizedPnl: 20, leverage: 1 },
    ],
    pnlDay: 40,
    pnlTotal: 40,
    maxDrawdown: 0.1,
    riskStatus: "ok",
    ts: 0,
  };

  it("computes gross exposure from positions", () => {
    // 1*120 + 2*40 = 200
    expect(grossExposure(account)).toBeCloseTo(200, 6);
    expect(exposurePct(account)).toBeCloseTo(200 / 2000, 6);
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
