/**
 * JEV V4.3 - independent, append-only strategy evidence chain.
 *
 * Kept OUT of paper_server_events on purpose: replay there reconstructs
 * account state from paper.server.account.snapshot events and must stay
 * byte-for-byte clean. Every row here links to the previous row's hash, so
 * auto-pause / verification decisions are independently replayable.
 */
import { sha256Hex } from "./service-auth.mjs";

export const STRATEGY_EVENT_TYPES = new Set([
  "strategy.equity.snapshot",
  "strategy.auto.paused",
  "strategy.verification.accepted",
  "strategy.verification.rejected",
  "strategy.gate.evaluated",
  "agent.run.trusted"
]);

export async function appendStrategyEvent(db, strategyId, eventType, payload, nowMs = Date.now()) {
  if (!STRATEGY_EVENT_TYPES.has(eventType)) {
    return { ok: false, error: "STRATEGY_EVENT_TYPE_NOT_ALLOWED" };
  }
  const last = await db.prepare("SELECT seq, hash FROM strategy_events ORDER BY seq DESC LIMIT 1").first();
  const prevHash = last ? String(last.hash) : null;
  const at = new Date(nowMs).toISOString();
  const payloadJson = JSON.stringify(payload == null ? {} : payload);
  const canonical = JSON.stringify({
    strategyId: String(strategyId), eventType, payloadJson, prevHash, at
  });
  const hash = await sha256Hex(canonical);
  await db.prepare(
    "INSERT INTO strategy_events (strategy_id, event_type, payload_json, prev_hash, hash, created_at) VALUES (?, ?, ?, ?, ?, ?)"
  ).bind(String(strategyId), eventType, payloadJson, prevHash, hash, at).run();
  return { ok: true, hash, prevHash, createdAt: at, eventType };
}

/** Re-verify the complete chain. Any broken link -> verified=false. */
export async function verifyStrategyEventChain(db) {
  const result = await db.prepare(
    "SELECT seq, strategy_id, event_type, payload_json, prev_hash, hash, created_at FROM strategy_events ORDER BY seq ASC"
  ).all();
  const rows = result.results || [];
  let prevHash = null;
  let verified = true;
  let brokenAt = null;
  for (const row of rows) {
    const canonical = JSON.stringify({
      strategyId: String(row.strategy_id), eventType: String(row.event_type),
      payloadJson: String(row.payload_json), prevHash, at: String(row.created_at)
    });
    const expected = await sha256Hex(canonical);
    if (row.prev_hash !== prevHash || row.hash !== expected) {
      verified = false;
      brokenAt = row.seq;
      break;
    }
    prevHash = row.hash;
  }
  return { verified, count: rows.length, lastSequence: rows.length ? rows[rows.length - 1].seq : 0,
           headHash: prevHash, brokenAt };
}

export const __test = { appendStrategyEvent, verifyStrategyEventChain, STRATEGY_EVENT_TYPES };
