// Shared fixtures: real migration SQL + stubs for the two frozen tables the
// attribution queries read (paper_fills, strategy_versions). The stubs exist
// ONLY in the in-memory test database; nothing is written to disk.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { D1Shim } from "./d1-shim.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const AI_DIR = join(HERE, "..", "..");
export const MIGRATION_PATH = join(AI_DIR, "migrations", "0006_ai_decisions.sql");

const STUB_DDL = [
  "CREATE TABLE IF NOT EXISTS strategy_versions (",
  "  strategy_id TEXT PRIMARY KEY NOT NULL, version TEXT NOT NULL, name TEXT NOT NULL,",
  "  family TEXT NOT NULL, source TEXT NOT NULL, source_sha256 TEXT NOT NULL,",
  "  params_json TEXT NOT NULL DEFAULT '{}', lifecycle TEXT NOT NULL DEFAULT 'candidate',",
  "  frozen INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);",
  "CREATE TABLE IF NOT EXISTS paper_fills (",
  "  fill_id TEXT PRIMARY KEY NOT NULL, order_id TEXT NOT NULL, fill_index INTEGER NOT NULL,",
  "  qty INTEGER NOT NULL, price INTEGER NOT NULL, notional INTEGER NOT NULL,",
  "  fee INTEGER NOT NULL, realized_pnl INTEGER, book_sequence INTEGER);",
].join("\n");

export function migrationSql() { return readFileSync(MIGRATION_PATH, "utf8"); }

export function makeDb() {
  return new D1Shim(migrationSql() + "\n" + STUB_DDL + "\n");
}

export function validHold(overrides = {}) {
  return {
    schemaVersion: "jev.ai.decision/v1",
    strategyId: "strat-mock-01",
    symbol: "BTCUSDT",
    action: "HOLD",
    side: null,
    orderType: null,
    quantity: null,
    limitPrice: null,
    confidence: 0.5,
    reason: "no edge",
    riskFlags: [],
    marketContextId: "mctx-test-0001",
    validForSeconds: 60,
    ...overrides,
  };
}

export function validBuy(overrides = {}) {
  return {
    schemaVersion: "jev.ai.decision/v1",
    strategyId: "strat-mock-01",
    symbol: "BTCUSDT",
    action: "BUY",
    side: "BUY",
    orderType: "MARKET",
    quantity: 0.001,
    limitPrice: null,
    confidence: 0.6,
    reason: "breakout",
    riskFlags: ["TEST"],
    marketContextId: "mctx-test-0001",
    validForSeconds: 60,
    ...overrides,
  };
}

export function makeContext(nowMs, overrides = {}) {
  return {
    marketContextId: "mctx-test-0001",
    symbol: "BTCUSDT",
    markPriceUsd: 60000,
    priceTs: new Date(nowMs).toISOString(),
    markQuality: "verified",
    source: "test://binance.us",
    sourceSha256: "a".repeat(64),
    ...overrides,
  };
}

/** Insert an approved, executable decision row directly (test setup only). */
export async function seedApprovedDecision(db, overrides = {}) {
  const { toDecisionRow, insertDecision, recordRiskVerdict } = await import("../../decision-store.mjs");
  const { validateDecision } = await import("../../decision-schema.mjs");
  const check = validateDecision(validBuy(overrides.decision));
  if (!check.valid) throw new Error("seed decision invalid: " + JSON.stringify(check.errors));
  const row = toDecisionRow(check.decision, {
    decisionId: overrides.decisionId || "dec-seed-0001",
    providerId: "mock",
    modelId: "mock-deterministic-v1",
    providerVersion: "test",
    inputSnapshotSha256: overrides.inputSnapshotSha256 || "b".repeat(64),
    requestPayloadSha256: "c".repeat(64),
    responsePayloadSha256: "d".repeat(64),
    priceTs: new Date(Date.now()).toISOString(),
    dataMode: overrides.dataMode || "mock",
    nowMs: Date.now(),
  });
  await insertDecision(db, row);
  await recordRiskVerdict(db, row.decision_id, { approved: true, code: "RISK_APPROVED", reason: "seeded" });
  return row.decision_id;
}
