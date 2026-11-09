// T6 - decision persistence and the decision -> order attribution chain.

import { createDecision, readRun, readPerformance, readStrategies, executeApprovedDecision } from "../runtime.mjs";
import { getDecision, linkOrder } from "../decision-store.mjs";
import { makeContext, makeDb, seedApprovedDecision } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t6_persistence_attribution";
export const title = "Persistence, hashes, run projection and attribution via decision_order_links";

const NOW = Date.parse("2026-11-09T12:00:00.000Z");

export async function run(suite) {
  await suite.check("decision row round-trips 1e8 fixed point and JSON flags", async () => {
    const db = makeDb();
    const r = await createDecision({ db, env: {}, strategyId: "strat-persist", symbol: "BTCUSDT", dataMode: "mock", marketContext: makeContext(NOW), nowMs: NOW, runId: "run-0001" });
    const stored = await getDecision(db, r.decision.decisionId);
    assert(stored, "row must exist");
    assertEqual(stored.strategyId, "strat-persist");
    assertEqual(stored.symbol, "BTCUSDT");
    assertEqual(stored.dataMode, "mock");
    assertEqual(stored.runId, "run-0001");
    assertEqual(stored.inputSnapshotSha256.length, 64);
    assertEqual(stored.requestPayloadSha256.length, 64);
    assertEqual(stored.responsePayloadSha256.length, 64);
    assertEqual(stored.priceTs, new Date(NOW).toISOString());
    assertEqual(Array.isArray(stored.riskFlags), true);
    if (stored.quantity !== null) {
      const raw = await db.prepare("SELECT quantity FROM ai_decisions WHERE decision_id = ?").bind(stored.decisionId).first();
      assertEqual(Number(raw.quantity), Math.round(stored.quantity * 1e8));
    }
    assert(stored.confidence >= 0 && stored.confidence <= 1, "confidence must round-trip inside [0,1]");
    return "row ok, status=" + stored.status;
  });

  await suite.check("run projection lists decisions with their links", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-run-0001" });
    await db.prepare("UPDATE ai_decisions SET run_id = 'run-A' WHERE decision_id = ?").bind(decisionId).run();
    await linkOrder(db, decisionId, "ord-run-1");
    const run = await readRun(db, "run-A");
    assertEqual(run.status, "RECORDED");
    assertEqual(run.count, 1);
    assertEqual(run.decisions[0].links.length, 1);
    assertEqual(run.decisions[0].links[0].order_id, "ord-run-1");
    const empty = await readRun(db, "run-none");
    assertEqual(empty.status, "NO_DATA");
    return "count=1 links=1";
  });

  await suite.check("execute + attribution yields real order/fill performance", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-perf-0001" });
    const submitOrder = async () => ({ httpStatus: 201, body: { ok: true, accepted: true, orderId: "ord-perf-1", fills: [{ fillId: "fill-perf-1" }] } });
    await executeApprovedDecision({ db, decisionId, submitOrder });
    await db.prepare("INSERT INTO paper_fills (fill_id, order_id, fill_index, qty, price, notional, fee, realized_pnl, book_sequence) VALUES (?,?,?,?,?,?,?,?,?)")
      .bind("fill-perf-1", "ord-perf-1", 1, 100000, 6000000000, 600000, 600, 12345678, 7).run();
    const perf = await readPerformance(db, {});
    assertEqual(perf.status, "RECORDED");
    assertEqual(perf.counts.decisions, 1);
    assertEqual(perf.counts.approved, 1);
    assertEqual(perf.counts.submitted, 1);
    assertEqual(perf.counts.linkedOrders, 1);
    assertEqual(perf.counts.fills, 1);
    assertEqual(perf.realizedPnl, 0.12345678);
    return "realizedPnl=0.12345678";
  });

  await suite.check("empty database reports NO_DATA, not zero performance", async () => {
    const db = makeDb();
    const perf = await readPerformance(db, {});
    assertEqual(perf.status, "NO_DATA");
    assertEqual(perf.counts.decisions, 0);
    assertEqual(perf.realizedPnl, 0);
    assert(/UNKNOWN/.test(perf.note), perf.note);
    return perf.status;
  });

  await suite.check("strategy filter and data_mode grouping are honoured", async () => {
    const db = makeDb();
    await seedApprovedDecision(db, { decisionId: "dec-g1" });
    await db.prepare("INSERT INTO ai_decisions (decision_id, strategy_id, provider_id, model_id, run_id, symbol, action, side, order_type, quantity, limit_price, confidence, reason, risk_flags_json, market_context_id, input_snapshot_sha256, prompt_version, request_payload_sha256, response_payload_sha256, price_ts, data_mode, risk_check_result, paper_order_id, paper_fill_ids_json, status, error_code, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
      .bind("dec-g2", "strat-other", "mock", "mock-deterministic-v1", null, "ETHUSDT", "HOLD", null, null, null, null, 50000000, "hold", "[]", "mctx-2", "f".repeat(64), "jev.ai.prompt.mock/v1", null, null, null, "mock", "NOT_RUN", null, "[]", "PENDING", null, new Date().toISOString()).run();
    const all = await readPerformance(db, {});
    assertEqual(all.counts.decisions, 2);
    const filtered = await readPerformance(db, { strategyId: "strat-other" });
    assertEqual(filtered.counts.decisions, 1);
    assertEqual(filtered.byStrategy.length, 2);
    assert(filtered.byStrategy.every((b) => b.dataMode === "mock"), "grouping must expose data_mode");
    return "filtered=1";
  });

  await suite.check("strategy listing is read-only and degrades to NO_DATA", async () => {
    const db = makeDb();
    const empty = await readStrategies(db);
    assertEqual(empty.status, "NO_DATA");
    assertEqual(empty.strategies.length, 0);
    return empty.status;
  });
}
