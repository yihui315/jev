/**
 * Snapshot builder. NUMERIC logic is REUSED (not rewritten) from the FROZEN
 * engine's pure `computeEquitySnapshot` / `equityInsertStatement` in
 * src/vendor/equity-snapshot.mjs (a byte-identical copy of the frozen module).
 *
 * We override ONLY the snapshot_id with a DETERMINISTIC id (the engine uses
 * crypto.randomUUID(), which would break idempotency) and switch the INSERT to
 * INSERT OR IGNORE. No frozen file is ever edited; T5 verifies the vendor
 * copies are byte-identical to the frozen originals via sha256.
 *
 * We deliberately do NOT call the frozen module's fetchMarks() /
 * collectEquitySnapshots(): those use the FORBIDDEN data-api.binance.vision
 * host. Marks come exclusively from src/market.mjs (Binance.US).
 */
import { computeEquitySnapshot, equityInsertStatement } from "./vendor/equity-snapshot.mjs";
import { D } from "./vendor/decimal.mjs";
import { deterministicSnapshotId } from "./hash.mjs";

/** 1e8 fixed-point integer of the account's initialCash (replay basis). */
export function initialCashFp(accountRow) {
  const account = JSON.parse(accountRow.state_json);
  const n = Number(D(account.initialCash || 0));
  if (!Number.isSafeInteger(n)) throw new Error("EQUITY_FIXED_POINT_OUT_OF_SAFE_RANGE");
  return n;
}

export async function buildSnapshot(accountRow, marksInfo, historyPeakFp, nowMs, idParts) {
  // Pure numeric computation from the frozen engine.
  const base = computeEquitySnapshot(accountRow, marksInfo, historyPeakFp, nowMs);
  const snapshotId = await deterministicSnapshotId({
    sessionId: idParts.sessionId ?? base.sessionId,
    generation: idParts.generation ?? base.generation,
    cycleKey: idParts.cycleKey,
    accountVersion: idParts.accountVersion ?? base.accountVersion,
    stateHash: idParts.stateHash ?? base.stateHash,
  });
  return { ...base, snapshotId, cycleKey: idParts.cycleKey };
}

/** Frozen-compatible INSERT, made idempotent. */
export function snapshotInsertStatement(snapshot) {
  const stmt = equityInsertStatement(snapshot);
  return {
    sql: stmt.sql.replace(/^INSERT INTO/, "INSERT OR IGNORE INTO"),
    values: stmt.values,
  };
}
