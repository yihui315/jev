// T4 - idempotency: one decision_id can never produce two paper orders.

import { executeApprovedDecision, createDecision } from "../runtime.mjs";
import { toDecisionRow, insertDecision, linkOrder, listLinks, getDecision } from "../decision-store.mjs";
import { validHold, makeContext, makeDb, seedApprovedDecision } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t4_idempotency";
export const title = "Execution idempotency and risk-approval precondition";

function countingEngine(orderId) {
  const state = { calls: 0, payloads: [] };
  return {
    state,
    submitOrder: async (payload) => {
      state.calls += 1;
      state.payloads.push(payload);
      return { httpStatus: 201, body: { ok: true, accepted: true, orderId, fills: [{ fillId: "fill-" + orderId }] } };
    },
  };
}

export async function run(suite) {
  await suite.check("first execute submits once and links the order", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-exec-0001" });
    const engine = countingEngine("ord-0001");
    const r = await executeApprovedDecision({ db, decisionId, submitOrder: engine.submitOrder });
    assertEqual(r.ok, true);
    assertEqual(r.duplicate, false);
    assertEqual(r.orderId, "ord-0001");
    assertEqual(engine.state.calls, 1);
    assertEqual(engine.state.payloads[0].clientOrderId, "ai-dec-exec-0001");
    assertEqual(engine.state.payloads[0].symbol, "BTCUSDT");
    const stored = await getDecision(db, decisionId);
    assertEqual(stored.status, "SUBMITTED");
    assertEqual(stored.paperOrderId, "ord-0001");
    assertEqual(JSON.stringify(stored.paperFillIds), JSON.stringify(["fill-ord-0001"]));
    return "calls=1";
  });

  await suite.check("second execute of the same decision_id does not call the engine", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-exec-0002" });
    const engine = countingEngine("ord-0002");
    await executeApprovedDecision({ db, decisionId, submitOrder: engine.submitOrder });
    const second = await executeApprovedDecision({ db, decisionId, submitOrder: engine.submitOrder });
    assertEqual(second.ok, true);
    assertEqual(second.duplicate, true);
    assertEqual(second.orderId, "ord-0002");
    assertEqual(engine.state.calls, 1);
    return "engine calls stayed at 1";
  });

  await suite.check("decision_order_links has exactly one row per (decision, order)", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-exec-0003" });
    const engine = countingEngine("ord-0003");
    await executeApprovedDecision({ db, decisionId, submitOrder: engine.submitOrder });
    const again = await linkOrder(db, decisionId, "ord-0003");
    assertEqual(again.linked, false);
    const links = await listLinks(db, decisionId);
    assertEqual(links.length, 1);
    return "UNIQUE enforced";
  });

  await suite.check("a risk-rejected decision cannot be executed", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-exec-0004" });
    await db.prepare("UPDATE ai_decisions SET risk_check_result = 'REJECT', status = 'REJECTED', error_code = 'RISK_CONTEXT_STALE' WHERE decision_id = ?").bind(decisionId).run();
    const engine = countingEngine("ord-0004");
    const r = await executeApprovedDecision({ db, decisionId, submitOrder: engine.submitOrder });
    assertEqual(r.ok, false);
    assertEqual(r.code, "EXECUTE_NOT_APPROVED");
    assertEqual(engine.state.calls, 0);
    return r.code + " engine calls=0";
  });

  await suite.check("a HOLD decision is not executable", async () => {
    const db = makeDb();
    const row = toDecisionRow(validHold(), {
      decisionId: "dec-hold-0001", providerId: "mock", modelId: "mock-deterministic-v1",
      inputSnapshotSha256: "e".repeat(64), dataMode: "mock", nowMs: Date.now(),
      marketContextId: "mctx-test-0001",
    });
    await insertDecision(db, row);
    const engine = countingEngine("ord-hold");
    const r = await executeApprovedDecision({ db, decisionId: "dec-hold-0001", submitOrder: engine.submitOrder });
    assertEqual(r.ok, false);
    assertEqual(r.code, "DECISION_IS_HOLD");
    assertEqual(engine.state.calls, 0);
    return r.code;
  });

  await suite.check("unknown decision_id returns 404 and never calls the engine", async () => {
    const db = makeDb();
    const engine = countingEngine("ord-none");
    const r = await executeApprovedDecision({ db, decisionId: "dec-missing", submitOrder: engine.submitOrder });
    assertEqual(r.ok, false);
    assertEqual(r.code, "DECISION_NOT_FOUND");
    assertEqual(engine.state.calls, 0);
    return r.code;
  });

  await suite.check("missing paper session (401) keeps the decision retryable", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-exec-0005" });
    let calls = 0;
    const submitOrder = async () => {
      calls += 1;
      if (calls === 1) return { httpStatus: 401, body: { ok: false, code: "PAPER_SESSION_REQUIRED" } };
      return { httpStatus: 201, body: { ok: true, accepted: true, orderId: "ord-0005", fills: [] } };
    };
    const first = await executeApprovedDecision({ db, decisionId, submitOrder });
    assertEqual(first.ok, false);
    assertEqual(first.retryable, true);
    const afterFirst = await getDecision(db, decisionId);
    assertEqual(afterFirst.status, "APPROVED");
    const second = await executeApprovedDecision({ db, decisionId, submitOrder });
    assertEqual(second.ok, true);
    assertEqual(second.orderId, "ord-0005");
    assertEqual(calls, 2);
    return "retry succeeded after session restored";
  });

  await suite.check("creating the same decision twice yields one row (duplicate flag)", async () => {
    const db = makeDb();
    const now = Date.parse("2026-11-09T12:00:00.000Z");
    const ctx = makeContext(now);
    const first = await createDecision({ db, env: {}, strategyId: "strat-dup", symbol: "BTCUSDT", dataMode: "mock", marketContext: ctx, nowMs: now });
    const second = await createDecision({ db, env: {}, strategyId: "strat-dup", symbol: "BTCUSDT", dataMode: "mock", marketContext: ctx, nowMs: now });
    assertEqual(first.duplicate, false);
    assertEqual(second.duplicate, true);
    assertEqual(first.decision.decisionId, second.decision.decisionId);
    const count = await db.prepare("SELECT COUNT(*) AS n FROM ai_decisions WHERE strategy_id = ?").bind("strat-dup").first();
    assertEqual(Number(count.n), 1);
    return "rows=1";
  });
}
